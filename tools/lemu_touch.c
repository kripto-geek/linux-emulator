/*
 * lemu_touch - create a *genuine* multitouch touchscreen inside an Android guest
 * and drive it from a simple line protocol on stdin.
 *
 * Why this exists
 * ---------------
 * QEMU's usb-tablet is reported to the guest as an absolute *mouse*, so games
 * that only listen to touch events ignore it.  The guest kernel, however, has
 * uinput built in (CONFIG_INPUT_UINPUT=y) and /dev/uinput available, so we can
 * materialise a real evdev touchscreen with INPUT_PROP_DIRECT + BTN_TOUCH +
 * ABS_MT_*, which Android's InputReader classifies as a TouchScreenInputMapper
 * with no IDC file required.
 *
 * Build (host):
 *   gcc -static -O2 -o lemu-touch lemu_touch.c
 *
 * The binary is statically linked so it runs on the guest's bionic userspace
 * (a static ELF needs no dynamic loader and talks to the kernel directly).
 *
 * Protocol (one command per line, whitespace separated; coordinates are
 * normalised 0.0-1.0 over the device's reported range):
 *   tap   <x> <y> [ms]              press+release in one spot
 *   swipe <x1> <y1> <x2> <y2> <ms> [steps]
 *   down  <x> <y>                   begin a drag
 *   move  <x> <y>                   move an active drag
 *   up                              end a drag
 *   ping                            liveness check
 *   quit                            destroy the device and exit
 *
 * "READY" is printed once the device exists, and "done" after every completed
 * gesture, so a caller can optionally synchronise on it.
 */
#define _GNU_SOURCE
#include <errno.h>
#include <fcntl.h>
#include <signal.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <time.h>
#include <unistd.h>

#include <linux/input.h>
#include <linux/uinput.h>
#include <sys/ioctl.h>

/* The device advertises a 0..32767 range on both axes.  Android maps that
 * range onto the touch surface itself, so normalised fractions are all we
 * need to send. */
#define TOUCH_RANGE 32767
#define MAX_STEPS 256

static int ufd = -1;
static int tracking_id = 0;

static void emit(int type, int code, int value)
{
    struct input_event ev;

    memset(&ev, 0, sizeof(ev));
    ev.type = type;
    ev.code = code;
    ev.value = value;
    if (write(ufd, &ev, sizeof(ev)) < 0 && errno != EAGAIN)
        perror("lemu-touch: write");
}

static void syn(void)
{
    emit(EV_SYN, SYN_REPORT, 0);
}

static void sleep_ms(long ms)
{
    struct timespec ts;

    if (ms <= 0)
        return;
    ts.tv_sec = ms / 1000;
    ts.tv_nsec = (ms % 1000) * 1000000L;
    nanosleep(&ts, NULL);
}

static double clamp01(double v)
{
    if (v < 0.0)
        return 0.0;
    if (v > 1.0)
        return 1.0;
    return v;
}

static int abs_of(double n)
{
    return (int)(clamp01(n) * TOUCH_RANGE + 0.5);
}

static int create_device(const char *name)
{
    static const int absbits[] = {
        ABS_X, ABS_Y,
        ABS_MT_SLOT, ABS_MT_TRACKING_ID,
        ABS_MT_POSITION_X, ABS_MT_POSITION_Y,
        ABS_MT_TOUCH_MAJOR, ABS_MT_PRESSURE,
    };
    struct uinput_user_dev udev;
    size_t i;

    ufd = open("/dev/uinput", O_WRONLY | O_NONBLOCK);
    if (ufd < 0) {
        perror("lemu-touch: open /dev/uinput");
        return -1;
    }

    ioctl(ufd, UI_SET_EVBIT, EV_KEY);
    ioctl(ufd, UI_SET_KEYBIT, BTN_TOUCH);
    ioctl(ufd, UI_SET_KEYBIT, BTN_TOOL_FINGER);

    ioctl(ufd, UI_SET_EVBIT, EV_ABS);
    for (i = 0; i < sizeof(absbits) / sizeof(absbits[0]); i++)
        ioctl(ufd, UI_SET_ABSBIT, absbits[i]);

    /* Direct input property = "this device is a touchscreen", the single bit
     * that makes Android's InputReader pick the touchscreen classifier. */
    ioctl(ufd, UI_SET_PROPBIT, INPUT_PROP_DIRECT);

    memset(&udev, 0, sizeof(udev));
    snprintf(udev.name, UINPUT_MAX_NAME_SIZE, "%s", name);
    udev.id.bustype = BUS_VIRTUAL;
    udev.id.vendor  = 0x1;
    udev.id.product = 0x1;
    udev.id.version = 1;

    udev.absmin[ABS_X] = 0;
    udev.absmax[ABS_X] = TOUCH_RANGE;
    udev.absmin[ABS_Y] = 0;
    udev.absmax[ABS_Y] = TOUCH_RANGE;
    udev.absmin[ABS_MT_SLOT] = 0;
    udev.absmax[ABS_MT_SLOT] = 9;
    udev.absmin[ABS_MT_TRACKING_ID] = 0;
    udev.absmax[ABS_MT_TRACKING_ID] = 65535;
    udev.absmin[ABS_MT_POSITION_X] = 0;
    udev.absmax[ABS_MT_POSITION_X] = TOUCH_RANGE;
    udev.absmin[ABS_MT_POSITION_Y] = 0;
    udev.absmax[ABS_MT_POSITION_Y] = TOUCH_RANGE;
    udev.absmin[ABS_MT_TOUCH_MAJOR] = 0;
    udev.absmax[ABS_MT_TOUCH_MAJOR] = 255;
    udev.absmin[ABS_MT_PRESSURE] = 0;
    udev.absmax[ABS_MT_PRESSURE] = 255;

    if (write(ufd, &udev, sizeof(udev)) < 0) {
        perror("lemu-touch: write uinput_user_dev");
        return -1;
    }
    if (ioctl(ufd, UI_DEV_CREATE) < 0) {
        perror("lemu-touch: UI_DEV_CREATE");
        return -1;
    }
    return 0;
}

static void touch_down(double nx, double ny)
{
    int x = abs_of(nx);
    int y = abs_of(ny);

    emit(EV_ABS, ABS_MT_SLOT, 0);
    emit(EV_ABS, ABS_MT_TRACKING_ID, ++tracking_id);
    emit(EV_KEY, BTN_TOUCH, 1);
    emit(EV_ABS, ABS_MT_POSITION_X, x);
    emit(EV_ABS, ABS_MT_POSITION_Y, y);
    emit(EV_ABS, ABS_X, x);
    emit(EV_ABS, ABS_Y, y);
    emit(EV_ABS, ABS_MT_TOUCH_MAJOR, 16);
    emit(EV_ABS, ABS_MT_PRESSURE, 128);
    syn();
}

static void touch_move(double nx, double ny)
{
    int x = abs_of(nx);
    int y = abs_of(ny);

    emit(EV_ABS, ABS_MT_SLOT, 0);
    emit(EV_ABS, ABS_MT_POSITION_X, x);
    emit(EV_ABS, ABS_MT_POSITION_Y, y);
    emit(EV_ABS, ABS_X, x);
    emit(EV_ABS, ABS_Y, y);
    syn();
}

static void touch_up(void)
{
    emit(EV_ABS, ABS_MT_SLOT, 0);
    emit(EV_ABS, ABS_MT_TRACKING_ID, -1);
    emit(EV_KEY, BTN_TOUCH, 0);
    syn();
}

static void do_swipe(double x1, double y1, double x2, double y2, long ms)
{
    long steps = ms / 8;
    long i;

    if (steps < 2)
        steps = 2;
    if (steps > MAX_STEPS)
        steps = MAX_STEPS;

    touch_down(x1, y1);
    for (i = 1; i <= steps; i++) {
        double t = (double)i / (double)steps;
        touch_move(x1 + (x2 - x1) * t, y1 + (y2 - y1) * t);
        sleep_ms(ms / steps);
    }
    touch_up();
}

static void on_signal(int sig)
{
    (void)sig;
    if (ufd >= 0) {
        ioctl(ufd, UI_DEV_DESTROY);
        close(ufd);
        ufd = -1;
    }
    _exit(0);
}

int main(int argc, char **argv)
{
    const char *name = argc > 1 ? argv[1] : "Lemu Touchscreen";
    char line[512];

    signal(SIGINT, on_signal);
    signal(SIGTERM, on_signal);
    signal(SIGHUP, on_signal);
    signal(SIGPIPE, SIG_IGN);

    if (create_device(name) < 0)
        return 1;

    /* The kernel allocates the evdev node synchronously, but ueventd needs a
     * moment to publish it; a short settle avoids racing the first gesture. */
    sleep_ms(250);

    printf("READY\n");
    fflush(stdout);

    while (fgets(line, sizeof(line), stdin)) {
        char cmd[32];
        double a, b, c, d;
        long ms;
        int n;

        n = sscanf(line, "%31s", cmd);
        if (n != 1)
            continue;

        if (!strcmp(cmd, "quit"))
            break;
        if (!strcmp(cmd, "ping")) {
            printf("pong\n");
            fflush(stdout);
            continue;
        }
        if (!strcmp(cmd, "tap")) {
            if (sscanf(line, "%*s %lf %lf %ld", &a, &b, &ms) < 2)
                ms = 40;
            touch_down(a, b);
            sleep_ms(ms);
            touch_up();
            printf("done\n");
            fflush(stdout);
            continue;
        }
        if (!strcmp(cmd, "swipe")) {
            if (sscanf(line, "%*s %lf %lf %lf %lf %ld", &a, &b, &c, &d, &ms) < 5)
                ms = 140;
            do_swipe(a, b, c, d, ms);
            printf("done\n");
            fflush(stdout);
            continue;
        }
        if (!strcmp(cmd, "down")) {
            if (sscanf(line, "%*s %lf %lf", &a, &b) == 2)
                touch_down(a, b);
            printf("done\n");
            fflush(stdout);
            continue;
        }
        if (!strcmp(cmd, "move")) {
            if (sscanf(line, "%*s %lf %lf", &a, &b) == 2)
                touch_move(a, b);
            printf("done\n");
            fflush(stdout);
            continue;
        }
        if (!strcmp(cmd, "up")) {
            touch_up();
            printf("done\n");
            fflush(stdout);
            continue;
        }
        fprintf(stderr, "lemu-touch: unknown command %s\n", cmd);
    }

    ioctl(ufd, UI_DEV_DESTROY);
    close(ufd);
    return 0;
}
