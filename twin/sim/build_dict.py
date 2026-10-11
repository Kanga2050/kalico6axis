#!/usr/bin/env python3
# Builds Kalico firmware for a chip and keeps its data dictionary, which the
# simulated MCU serves so the host sees exactly the real command set and pins.
import os
import re
import shutil
import subprocess
import sys

KALICO = os.environ.get("KLIPPY_DIR", "/opt/printer/kalico")


def kconfig(chip):
    c = chip.lower()
    if c in ("linux", "host"):
        return ["CONFIG_MACH_LINUX=y"]
    if c.startswith("stm32"):
        model = re.sub(r"x+$", "", c).upper()
        lines = ["CONFIG_MACH_STM32=y", "CONFIG_MACH_%s=y" % model]
        if model.startswith(("STM32F4", "STM32F7", "STM32H7", "STM32G4", "STM32F0", "STM32G0")):
            lines.append("CONFIG_STM32_USB_PA11_PA12=y")
        return lines
    if c in ("rp2040", "rp2350"):
        return ["CONFIG_MACH_RPXXXX=y", "CONFIG_MACH_%s=y" % c.upper()]
    if c.startswith("lpc17"):
        return ["CONFIG_MACH_LPC176X=y", "CONFIG_MACH_%s=y" % c.upper()]
    if c.startswith("atmega") or c.startswith("at90"):
        return ["CONFIG_MACH_AVR=y", "CONFIG_MACH_%s=y" % c]
    if c.startswith("samd") or c.startswith("same5"):
        return ["CONFIG_MACH_ATSAMD=y", "CONFIG_MACH_%s=y" % c.upper()]
    if c.startswith("sam3") or c.startswith("sam4"):
        return ["CONFIG_MACH_ATSAM=y", "CONFIG_MACH_%s=y" % c.upper()]
    raise SystemExit("don't know how to build firmware for chip '%s'" % chip)


def build(chip, out_path):
    work = "/tmp/build-%s" % chip
    if os.path.exists(work):
        shutil.rmtree(work)
    shutil.copytree(KALICO, work, ignore=shutil.ignore_patterns("out", ".git", "docs", "test", "*.pyc"))
    # every optional feature on, so any config the host sends is understood
    extras = ["CONFIG_LOW_LEVEL_OPTIONS=y", "CONFIG_HIGH_PREC_STEP=y"] + [
        "CONFIG_WANT_%s=y" % w for w in (
            "ADXL345", "LIS2DW", "MPU9250", "ICM20948", "THERMOCOUPLE", "HX71X", "ADS1220", "LDC1612",
            "SENSOR_ANGLE", "ST7920", "HD44780", "PULSE_COUNTER", "BUTTONS", "NEOPIXEL", "TMCUART",
            "SOFTWARE_SPI", "SOFTWARE_I2C", "GPIO_BITBANGING")]
    with open(os.path.join(work, ".config"), "w") as f:
        f.write("\n".join(kconfig(chip) + extras) + "\n")
    env = dict(os.environ, PYTHON=sys.executable)
    subprocess.run(["make", "olddefconfig"], cwd=work, env=env, check=True, stdout=subprocess.DEVNULL)
    subprocess.run(["make", "-j%d" % (os.cpu_count() or 2)], cwd=work, env=env, check=True, stdout=subprocess.DEVNULL)
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    shutil.copy(os.path.join(work, "out", "klipper.dict"), out_path)
    shutil.rmtree(work, ignore_errors=True)


if __name__ == "__main__":
    build(sys.argv[1], sys.argv[2])
