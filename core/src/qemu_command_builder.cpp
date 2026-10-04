// DroidForge core - QEMU command-line builder implementation.
//
// Design notes:
//   * The per-instance overlay (a qcow2 linked clone of the read-only base)
//     is created out-of-band with `qemu-img create -b base -f qcow2`. The
//     builder only references it; it never creates disk images.
//   * After the first install the overlay is the boot disk. For the very
//     first boot the *ISO* is attached as an extra drive (see bootIso flag);
//     Android-x86's GRUB offers "Install" from it.
//   * Everything emitted here is QEMU >= 8 syntax (we target 11.1).

#include "droidforge/qemu_command_builder.hpp"

#include <stdexcept>
#include <string>

namespace droidforge {

QemuCommandBuilder::QemuCommandBuilder(std::string qemu_bin)
    : qemu_bin_(std::move(qemu_bin)) {}

void QemuCommandBuilder::validate(const InstanceConfig& cfg) const {
    if (cfg.instance_id.empty())
        throw std::invalid_argument("instance_id must be non-empty");
    if (cfg.ram_mb < 256)
        throw std::invalid_argument("ram_mb must be >= 256");
    if (cfg.cpu_cores < 1)
        throw std::invalid_argument("cpu_cores must be >= 1");
    if (cfg.cpu_threads < 1)
        throw std::invalid_argument("cpu_threads must be >= 1");
    if (cfg.width < 320 || cfg.height < 240)
        throw std::invalid_argument("resolution too small");
    if (cfg.host_adb_port < 1024 || cfg.host_adb_port > 65535)
        throw std::invalid_argument("host_adb_port out of range");
    if (cfg.overlay_size_bytes <= 0)
        throw std::invalid_argument("overlay_size_bytes must be positive");
    if (cfg.ram_mb < 0)
        throw std::invalid_argument("ram_mb must be >= 0");
}

std::string QemuCommandBuilder::shellQuote(const std::string& s) const {
    if (s.empty()) return "\"\"";
    std::string out = "\"";
    for (char c : s) {
        if (c == '"' || c == '\\' || c == '$' || c == '`')
            out += '\\';
        out += c;
    }
    out += "\"";
    return out;
}

std::string QemuCommandBuilder::join(const std::vector<std::string>& args) const {
    std::string out;
    for (size_t i = 0; i < args.size(); ++i) {
        if (i) out += ' ';
        out += shellQuote(args[i]);
    }
    return out;
}

std::vector<std::string> QemuCommandBuilder::machine(const InstanceConfig& cfg) const {
    // q35: modern PCI topology required by virtio-gpu-gl-pci, clean ACPI/UEFI
    // layout for Android-x86, and correct device enumeration.
    std::vector<std::string> a;
    a.push_back("-machine");
    a.push_back("q35,accel=kvm");
    // android-x86 / Bliss expects ACPI timer + a standard RTC.
    a.push_back("-rtc");
    a.push_back("base=localtime,driftfix=slew");
    // Carry the instance id into SMBIOS so the guest (and our tools) can tell
    // which instance they are talking to. Readable in-guest via
    // `dmidecode -s system-serial` or `cat /sys/class/dmi/id/chassis...`.
    a.push_back("-smbios");
    a.push_back("type=1,manufacturer=DroidForge,product=DroidForge,"
                "serial=" + cfg.instance_id);
    return a;
}

std::vector<std::string> QemuCommandBuilder::memory(const InstanceConfig& cfg) const {
    std::vector<std::string> a;
    a.push_back("-m");
    a.push_back(std::to_string(cfg.ram_mb));
    a.push_back("-smp");
    a.push_back("cpus=" + std::to_string(cfg.cpu_cores) +
               ",cores=" + std::to_string(cfg.cpu_cores) +
               ",threads=" + std::to_string(cfg.cpu_threads) +
               ",sockets=1");
    return a;
}

std::vector<std::string> QemuCommandBuilder::cpu(const InstanceConfig& cfg) const {
    std::vector<std::string> a;
    a.push_back("-cpu");
    a.push_back(cfg.cpu_model);
    return a;
}

std::vector<std::string> QemuCommandBuilder::disk(const InstanceConfig& cfg) const {
    // Primary writable disk = the per-instance overlay (linked clone).
    // cache=none + aio=threads keeps I/O out of the main loop and direct to disk.
    std::vector<std::string> a;
    a.push_back("-drive");
    a.push_back("file=" + cfg.overlay_image +
                ",if=virtio,format=qcow2,cache=none,aio=threads,detect_zeroes=on");
    return a;
}

std::vector<std::string> QemuCommandBuilder::bootIso(const std::string& iso_path) const {
    // Second, read-only drive for the install medium (Android-x86 .iso).
    // Boot order is set by the guest's GRUB; we just expose the ISO.
    std::vector<std::string> a;
    a.push_back("-drive");
    a.push_back("file=" + iso_path +
                ",if=virtio,media=cdrom,readonly=on");
    a.push_back("-cdrom");
    a.push_back(iso_path);
    return a;
}

std::vector<std::string> QemuCommandBuilder::gpu(const InstanceConfig& cfg) const {
    std::vector<std::string> a;
    std::string memmb = "mem-mb=" + std::to_string(cfg.gpu_mem_mb);
    std::string scanouts = "max_scanouts=" + std::to_string(cfg.max_scanlines);
    switch (cfg.gpu_mode) {
        case GpuMode::Virgl:
            a = {"-device", "virtio-gpu-gl-pci," + memmb + "," + scanouts + ",gl=max"};
            break;
        case GpuMode::VirtioGpu:
            a = {"-device", "virtio-gpu-pci," + memmb + "," + scanouts};
            break;
        case GpuMode::Veniam:
            // Venus (Vulkan) - experimental. Emits virtio-vulkan-pci when the
            // build supports it; otherwise the boot script downgrades to virgl.
            a = {"-device", "virtio-vulkan-pci," + memmb};
            break;
    }
    return a;
}

std::vector<std::string> QemuCommandBuilder::display(const InstanceConfig& cfg) const {
    std::vector<std::string> a;
    switch (cfg.display) {
        case DisplayBackend::Sdl:
            a = {"-display", "sdl",
                 "-window", "width=" + std::to_string(cfg.width) +
                            ",height=" + std::to_string(cfg.height)};
            break;
        case DisplayBackend::Gtk:
            a = {"-display", "gtk",
                 "-window", "width=" + std::to_string(cfg.width) +
                            ",height=" + std::to_string(cfg.height)};
            break;
        case DisplayBackend::Headless:
            a = {"-display", "none"};
            break;
    }
    return a;
}

std::vector<std::string> QemuCommandBuilder::audio(const InstanceConfig& cfg) const {
    std::vector<std::string> a;
    if (!cfg.audio_enabled) return a;
    // QEMU 8+ unified -audio. PipeWire first, PulseAudio fallback.
    a.push_back("-audio");
    a.push_back("driver=" + cfg.audio_driver +
                ",in.engines="",out.engines="",in.device=default,out.device=default");
    // The guest still needs an HDA controller to consume the audio backend.
    a.push_back("-device");
    a.push_back("hda-duplex");
    return a;
}

std::vector<std::string> QemuCommandBuilder::network(const InstanceConfig& cfg) const {
    // User-mode (slirp). Host-forward the ADB port so
    //   adb connect 127.0.0.1:<host_adb_port>
    // reaches the guest's 5555.
    std::vector<std::string> a;
    std::string netdev = "user,id=net0,hostfwd=tcp:127.0.0.1:" +
                         std::to_string(cfg.host_adb_port) + "-:5555";
    a.push_back("-netdev");
    a.push_back(netdev);
    a.push_back("-device");
    a.push_back("virtio-net-pci,netdev=net0");
    return a;
}

std::vector<std::string> QemuCommandBuilder::qmp(const InstanceConfig& cfg) const {
    std::vector<std::string> a;
    if (cfg.qmp_socket.empty()) return a;
    a = {"-qmp", "unix:" + cfg.qmp_socket + ",server=on,wait=off"};
    return a;
}

std::vector<std::string> QemuCommandBuilder::build(const InstanceConfig& cfg,
                                                   const std::string& iso_path) const {
    validate(cfg);

    std::vector<std::string> argv{qemu_bin_};

    if (cfg.kvm_enabled) argv.push_back("-enable-kvm");

    auto append = [&argv](std::vector<std::string> seg) {
        for (auto& s : seg) argv.push_back(std::move(s));
    };

    append(machine(cfg));
    append(memory(cfg));
    append(cpu(cfg));
    append(disk(cfg));
    if (!iso_path.empty()) append(bootIso(iso_path));
    append(gpu(cfg));
    append(display(cfg));
    append(audio(cfg));
    append(network(cfg));
    append(qmp(cfg));

    // Start paused. Phase 2/3 will issue QMP "cont" once the QMP socket is
    // ready, so we can confirm boot progress before releasing the guest.
    argv.push_back("-S");

    return argv;
}

std::string QemuCommandBuilder::buildCommandLine(const InstanceConfig& cfg,
                                                 const std::string& iso_path) const {
    auto argv = build(cfg, iso_path);
    return join(argv);
}

} // namespace droidforge
