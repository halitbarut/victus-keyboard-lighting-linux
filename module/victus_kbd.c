// SPDX-License-Identifier: GPL-2.0 OR MIT
/*
 * HP Victus / Omen keyboard RGB backlight control via HP BIOS WMI.
 *
 * Exposes:
 *   /sys/devices/platform/victus-kbd/color       "RRGGBB" (all zones)
 *   /sys/devices/platform/victus-kbd/brightness  0 (off) / 1 (on)
 *   /sys/devices/platform/victus-kbd/brightness_raw  firmware value (diagnostics)
 *
 * Note: some firmware ignores the brightness switch; userspace should send
 * black (000000) to turn the light off reliably.
 */
#include <linux/acpi.h>
#include <linux/module.h>
#include <linux/platform_device.h>
#include <linux/slab.h>
#include <linux/wmi.h>
#include <linux/mutex.h>

#define HPWMI_BIOS_GUID		"5FB7F034-2C63-45E9-BE91-3D44E2C707E4"
#define HPWMI_SIGNATURE		0x55434553
#define HPWMI_FOURZONE		0x20009

#define FZ_COLOR_GET		0x02
#define FZ_COLOR_SET		0x03
#define FZ_BRIGHT_GET		0x04
#define FZ_BRIGHT_SET		0x05

#define FZ_BUF_SIZE		128
#define FZ_COLOR_OFFSET		25
#define FZ_ZONES		4

#define FZ_BRIGHT_ON		0xE4
#define FZ_BRIGHT_OFF		0x64

struct bios_args {
	u32 signature;
	u32 command;
	u32 commandtype;
	u32 datasize;
	u8 data[];
} __packed;

struct bios_return {
	u32 sigpass;
	u32 return_code;
} __packed;

static struct platform_device *vk_pdev;
static DEFINE_MUTEX(vk_lock);

static int vk_method_id(int outsize)
{
	if (outsize > 1024)
		return 5;
	if (outsize > 128)
		return 4;
	if (outsize > 4)
		return 3;
	if (outsize > 0)
		return 2;
	return 1;
}

static int vk_query(u32 cmdtype, void *buf, int insize, int outsize)
{
	struct acpi_buffer output = { ACPI_ALLOCATE_BUFFER, NULL };
	struct acpi_buffer input;
	struct bios_args *args;
	struct bios_return *br;
	union acpi_object *obj;
	acpi_status status;
	int ret;

	args = kzalloc(sizeof(*args) + insize, GFP_KERNEL);
	if (!args)
		return -ENOMEM;

	args->signature = HPWMI_SIGNATURE;
	args->command = HPWMI_FOURZONE;
	args->commandtype = cmdtype;
	args->datasize = insize;
	memcpy(args->data, buf, insize);

	input.length = sizeof(*args) + insize;
	input.pointer = args;

	status = wmi_evaluate_method(HPWMI_BIOS_GUID, 0, vk_method_id(outsize),
				     &input, &output);
	kfree(args);
	if (ACPI_FAILURE(status))
		return -EIO;

	obj = output.pointer;
	if (!obj)
		return -EINVAL;
	if (obj->type != ACPI_TYPE_BUFFER ||
	    obj->buffer.length < sizeof(*br)) {
		ret = -EINVAL;
		goto out;
	}

	br = (struct bios_return *)obj->buffer.pointer;
	ret = br->return_code;
	if (ret) {
		pr_debug("victus_kbd: cmdtype 0x%x returned %d\n", cmdtype, ret);
		ret = -EIO;
		goto out;
	}

	if (outsize) {
		memset(buf, 0, outsize);
		memcpy(buf, obj->buffer.pointer + sizeof(*br),
		       min_t(int, outsize, obj->buffer.length - sizeof(*br)));
	}
out:
	kfree(obj);
	return ret;
}

static ssize_t color_show(struct device *dev, struct device_attribute *attr,
			  char *page)
{
	u8 buf[FZ_BUF_SIZE] = {};
	int ret;

	mutex_lock(&vk_lock);
	ret = vk_query(FZ_COLOR_GET, buf, sizeof(buf), sizeof(buf));
	mutex_unlock(&vk_lock);
	if (ret)
		return ret;

	return sysfs_emit(page, "%02x%02x%02x\n", buf[FZ_COLOR_OFFSET],
			  buf[FZ_COLOR_OFFSET + 1], buf[FZ_COLOR_OFFSET + 2]);
}

static ssize_t color_store(struct device *dev, struct device_attribute *attr,
			   const char *page, size_t count)
{
	u8 buf[FZ_BUF_SIZE] = {};
	u32 rgb;
	int i, ret;

	if (kstrtou32(page, 16, &rgb) || rgb > 0xFFFFFF)
		return -EINVAL;

	mutex_lock(&vk_lock);
	ret = vk_query(FZ_COLOR_GET, buf, sizeof(buf), sizeof(buf));
	if (!ret) {
		for (i = 0; i < FZ_ZONES; i++) {
			buf[FZ_COLOR_OFFSET + i * 3 + 0] = (rgb >> 16) & 0xFF;
			buf[FZ_COLOR_OFFSET + i * 3 + 1] = (rgb >> 8) & 0xFF;
			buf[FZ_COLOR_OFFSET + i * 3 + 2] = rgb & 0xFF;
		}
		ret = vk_query(FZ_COLOR_SET, buf, sizeof(buf), sizeof(buf));
	}
	mutex_unlock(&vk_lock);

	return ret ? ret : count;
}
static DEVICE_ATTR_RW(color);

static ssize_t brightness_show(struct device *dev,
			       struct device_attribute *attr, char *page)
{
	u8 val[4] = {};
	int ret;

	mutex_lock(&vk_lock);
	ret = vk_query(FZ_BRIGHT_GET, val, sizeof(val), sizeof(val));
	mutex_unlock(&vk_lock);
	if (ret)
		return ret;

	return sysfs_emit(page, "%d\n", val[0] == FZ_BRIGHT_ON);
}

static ssize_t brightness_store(struct device *dev,
				struct device_attribute *attr,
				const char *page, size_t count)
{
	u8 val[4] = {};
	bool on;
	int ret;

	if (kstrtobool(page, &on))
		return -EINVAL;

	val[0] = on ? FZ_BRIGHT_ON : FZ_BRIGHT_OFF;
	mutex_lock(&vk_lock);
	ret = vk_query(FZ_BRIGHT_SET, val, sizeof(val), 0);
	mutex_unlock(&vk_lock);

	return ret ? ret : count;
}
static DEVICE_ATTR_RW(brightness);

/* Raw firmware backlight byte, for diagnosing models with other values */
static ssize_t brightness_raw_show(struct device *dev,
				   struct device_attribute *attr, char *page)
{
	u8 val[4] = {};
	int ret;

	mutex_lock(&vk_lock);
	ret = vk_query(FZ_BRIGHT_GET, val, sizeof(val), sizeof(val));
	mutex_unlock(&vk_lock);
	if (ret)
		return ret;

	return sysfs_emit(page, "0x%02x\n", val[0]);
}
static DEVICE_ATTR_RO(brightness_raw);

static struct attribute *vk_attrs[] = {
	&dev_attr_color.attr,
	&dev_attr_brightness.attr,
	&dev_attr_brightness_raw.attr,
	NULL
};
ATTRIBUTE_GROUPS(vk);

static int __init vk_init(void)
{
	u8 buf[FZ_BUF_SIZE] = {};
	int ret;

	if (!wmi_has_guid(HPWMI_BIOS_GUID))
		return -ENODEV;

	ret = vk_query(FZ_COLOR_GET, buf, sizeof(buf), sizeof(buf));
	if (ret) {
		pr_err("victus_kbd: keyboard color query failed (%d)\n", ret);
		return -ENODEV;
	}
	pr_info("victus_kbd: zone0 color %02x%02x%02x\n", buf[FZ_COLOR_OFFSET],
		buf[FZ_COLOR_OFFSET + 1], buf[FZ_COLOR_OFFSET + 2]);

	vk_pdev = platform_device_register_full(&(struct platform_device_info){
		.name = "victus-kbd",
		.id = PLATFORM_DEVID_NONE,
	});
	if (IS_ERR(vk_pdev))
		return PTR_ERR(vk_pdev);

	ret = device_add_groups(&vk_pdev->dev, vk_groups);
	if (ret) {
		platform_device_unregister(vk_pdev);
		return ret;
	}
	return 0;
}

static void __exit vk_exit(void)
{
	device_remove_groups(&vk_pdev->dev, vk_groups);
	platform_device_unregister(vk_pdev);
}

module_init(vk_init);
module_exit(vk_exit);

MODULE_DESCRIPTION("HP Victus keyboard RGB backlight control");
MODULE_VERSION("1.0.0");
/* Dual licensed; GPL compatibility is needed for the WMI symbols. */
MODULE_LICENSE("Dual MIT/GPL");
/*
 * No MODULE_ALIAS on purpose: the HP BIOS WMI GUID exists on every HP laptop,
 * so the module is only loaded explicitly by victus-kbd.service.
 */
