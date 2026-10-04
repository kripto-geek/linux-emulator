// Unit tests for droidforge::QemuCommandBuilder.
//
// We do not launch QEMU here. We only check the argv[] we emit is the argv[]
// we *want* to emit: right device names, right ports, right flags.

#include "droidforge/qemu_command_builder.hpp"

#include <gtest/gtest.h>

#include <algorithm>
#include <string>
#include <vector>

using droidforge::GpuMode;
using droidforge::QemuCommandBuilder;

namespace {

std::vector<std::string> defaults() {
    droidforge::InstanceConfig cfg;
    cfg.instance_id   = "pubg1";
    cfg.base_image    = "/opt/droidforge/base.qcow2";
    cfg.overlay_image = "/var/lib/droidforge/pubg1/overlay.qcow2";
    cfg.cpu_cores     = 4;
    cfg.ram_mb        = 4096;
    cfg.width         = 1280;
    cfg.height        = 720;
    cfg.gpu_mode      = GpuMode::Virgl;
    cfg.host_adb_port = 5555;
    cfg.qmp_socket    = "/run/user/1000/droidforge/pubg1/qmp.sock";
    QemuCommandBuilder builder;
    return builder.build(cfg, "/opt/droidforge/bliss-16.9.7.iso");
}

bool hasPair(const std::vector<std::string>& argv, const std::string& key,
             const std::string& value) {
    for (size_t i = 0; i + 1 < argv.size(); ++i)
        if (argv[i] == key && argv[i + 1] == value) return true;
    return false;
}

std::string valueAfter(const std::vector<std::string>& argv,
                       const std::string& key) {
    for (size_t i = 0; i + 1 < argv.size(); ++i)
        if (argv[i] == key) return argv[i + 1];
    return "";
}

} // namespace

TEST(QemuCommandBuilder, DefaultBuildHasKvm) {
    auto argv = defaults();
    EXPECT_EQ(argv.front(), "qemu-system-x86_64");
    EXPECT_EQ(argv[1], "-enable-kvm");
}

TEST(QemuCommandBuilder, MachineIsQ35) {
    auto argv = defaults();
    EXPECT_TRUE(hasPair(argv, "-machine", "q35,accel=kvm"));
}

TEST(QemuCommandBuilder, MemoryAndSmp) {
    auto argv = defaults();
    EXPECT_TRUE(hasPair(argv, "-m", "4096"));
    EXPECT_EQ(valueAfter(argv, "-smp"), "cpus=4,cores=4,threads=1,sockets=1");
}

TEST(QemuCommandBuilder, CpuModelHost) {
    auto argv = defaults();
    EXPECT_EQ(valueAfter(argv, "-cpu"), "host");
}

TEST(QemuCommandBuilder, DiskIsOverlayWithDirectCache) {
    auto argv = defaults();
    EXPECT_EQ(valueAfter(argv, "-drive"),
              "file=/var/lib/droidforge/pubg1/overlay.qcow2,"
              "if=virtio,format=qcow2,cache=none,aio=threads");
}

TEST(QemuCommandBuilder, IsoAttachedOnFirstBoot) {
    auto argv = defaults();
    EXPECT_TRUE(std::any_of(argv.begin(), argv.end(),
        [](const std::string& s) {
            return s == "file=/opt/droidforge/bliss-16.9.7.iso,if=virtio,"
                       "media=cdrom,readonly=on";
        }));
    EXPECT_EQ(valueAfter(argv, "-cdrom"), "/opt/droidforge/bliss-16.9.7.iso");
}

TEST(QemuCommandBuilder, NoIsoOnSubsequentBoot) {
    droidforge::InstanceConfig cfg;
    cfg.instance_id   = "a";
    cfg.overlay_image = "/tmp/ov.qcow2";
    QemuCommandBuilder builder;
    auto argv = builder.build(cfg, "");
    for (size_t i = 0; i + 1 < argv.size(); ++i)
        EXPECT_NE(argv[i], "-cdrom");
}

TEST(QemuCommandBuilder, VirglGpuDevice) {
    auto argv = defaults();
    // QEMU 11: no `gl`/`mem-mb`/`max_scanouts` properties on the device.
    // GL is enabled on the display backend (see SdlDisplayBackend).
    EXPECT_EQ(valueAfter(argv, "-device"), "virtio-gpu-gl-pci");
}

TEST(QemuCommandBuilder, GpuFallbackToVirtioGpu) {
    droidforge::InstanceConfig cfg;
    cfg.instance_id   = "a";
    cfg.overlay_image = "/tmp/ov.qcow2";
    cfg.gpu_mode      = GpuMode::VirtioGpu;
    QemuCommandBuilder builder;
    auto argv = builder.build(cfg);
    EXPECT_EQ(valueAfter(argv, "-device"), "virtio-gpu-pci");
}

TEST(QemuCommandBuilder, SdlDisplayBackend) {
    auto argv = defaults();
    // Virgl GPU mode -> GL enabled on the SDL display backend.
    EXPECT_EQ(valueAfter(argv, "-display"), "sdl,gl=on");
    // QEMU 11 has no -window flag; the window sizes to the guest framebuffer.
    for (size_t i = 0; i < argv.size(); ++i)
        EXPECT_NE(argv[i], "-window");
}

TEST(QemuCommandBuilder, SdlNoGlWhenNotVirgl) {
    droidforge::InstanceConfig cfg;
    cfg.instance_id   = "a";
    cfg.overlay_image = "/tmp/ov.qcow2";
    cfg.gpu_mode      = GpuMode::VirtioGpu;
    QemuCommandBuilder b;
    auto argv = b.build(cfg);
    EXPECT_EQ(valueAfter(argv, "-display"), "sdl");
}

TEST(QemuCommandBuilder, PipeWireAudioAndHda) {
    auto argv = defaults();
    // QEMU 11 unified -audio; in/out engine+device options omitted (QEMU
    // rejects the in.engines= keys). PipeWire driver + HDA controller.
    EXPECT_EQ(valueAfter(argv, "-audio"), "driver=pipewire");
    // HDA bus + codec both present.
    EXPECT_TRUE(std::any_of(argv.begin(), argv.end(),
        [](const std::string& s) { return s == "ich9-intel-hda"; }));
    EXPECT_TRUE(std::any_of(argv.begin(), argv.end(),
        [](const std::string& s) { return s == "hda-duplex"; }));
}

TEST(QemuCommandBuilder, AudioDisabledDropsAudio) {
    droidforge::InstanceConfig cfg;
    cfg.instance_id   = "a";
    cfg.overlay_image = "/tmp/ov.qcow2";
    cfg.audio_enabled = false;
    QemuCommandBuilder builder;
    auto argv = builder.build(cfg);
    for (size_t i = 0; i < argv.size(); ++i)
        EXPECT_NE(argv[i], "-audio");
}

TEST(QemuCommandBuilder, NetworkHostfwdForAdb) {
    auto argv = defaults();
    EXPECT_EQ(valueAfter(argv, "-netdev"),
              "user,id=net0,hostfwd=tcp:127.0.0.1:5555-:5555");
    EXPECT_TRUE(std::any_of(argv.begin(), argv.end(),
        [](const std::string& s) { return s == "virtio-net-pci,netdev=net0"; }));
}

TEST(QemuCommandBuilder, CustomAdbPort) {
    droidforge::InstanceConfig cfg;
    cfg.instance_id   = "a";
    cfg.overlay_image = "/tmp/ov.qcow2";
    cfg.host_adb_port = 5590;
    QemuCommandBuilder builder;
    auto argv = builder.build(cfg);
    EXPECT_EQ(valueAfter(argv, "-netdev"),
              "user,id=net0,hostfwd=tcp:127.0.0.1:5590-:5555");
}

TEST(QemuCommandBuilder, QmpSocket) {
    auto argv = defaults();
    EXPECT_EQ(valueAfter(argv, "-qmp"),
              "unix:/run/user/1000/droidforge/pubg1/qmp.sock,server=on,wait=off");
}

TEST(QemuCommandBuilder, NoQmpWhenEmpty) {
    droidforge::InstanceConfig cfg;
    cfg.instance_id   = "a";
    cfg.overlay_image = "/tmp/ov.qcow2";
    QemuCommandBuilder builder;
    auto argv = builder.build(cfg);
    for (size_t i = 0; i < argv.size(); ++i)
        EXPECT_NE(argv[i], "-qmp");
}

TEST(QemuCommandBuilder, PausedAtStart) {
    auto argv = defaults();
    EXPECT_EQ(argv.back(), "-S");
}

TEST(QemuCommandBuilder, ValidatesRamFloor) {
    droidforge::InstanceConfig cfg;
    cfg.instance_id   = "a";
    cfg.overlay_image = "/tmp/ov.qcow2";
    cfg.ram_mb        = 128;
    QemuCommandBuilder builder;
    EXPECT_THROW(builder.build(cfg), std::invalid_argument);
}

TEST(QemuCommandBuilder, ValidatesPortRange) {
    droidforge::InstanceConfig cfg;
    cfg.instance_id   = "a";
    cfg.overlay_image = "/tmp/ov.qcow2";
    cfg.host_adb_port = 80;
    QemuCommandBuilder builder;
    EXPECT_THROW(builder.build(cfg), std::invalid_argument);
}

TEST(QemuCommandBuilder, RejectsEmptyInstanceId) {
    droidforge::InstanceConfig cfg;
    cfg.overlay_image = "/tmp/ov.qcow2";
    QemuCommandBuilder builder;
    EXPECT_THROW(builder.build(cfg), std::invalid_argument);
}


TEST(QemuCommandBuilder, SmbiosCarriesInstanceId) {
    auto argv = defaults();
    EXPECT_EQ(valueAfter(argv, "-smbios"),
              "type=1,manufacturer=DroidForge,product=DroidForge,serial=pubg1");
}

TEST(QemuCommandBuilder, SmartedPathsAreQuotedInCommandLine) {
    droidforge::InstanceConfig cfg;
    cfg.instance_id   = "a path";
    cfg.overlay_image = "/tmp/a base image.qcow2";
    QemuCommandBuilder builder;
    auto line = builder.buildCommandLine(cfg);
    // The path is embedded in the -drive spec; the whole drive value must be shell-quoted.
    // The -drive value is a single shell-quoted token containing the space-
    // bearing path; verify it is wrapped in double quotes.
    EXPECT_NE(line.find("\"file=/tmp/a base image"), std::string::npos);
}
