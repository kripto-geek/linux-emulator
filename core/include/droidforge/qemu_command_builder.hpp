#pragma once

// DroidForge core - QEMU command-line builder.
//
// Pure, UI-independent, and dependency-free: it turns an InstanceConfig into
// the exact argv[] QEMU needs. This is the part that is unit-tested without
// launching any hypervisor.

#include <cstddef>
#include <cstdint>
#include <string>
#include <vector>

namespace droidforge {

// ---------------------------------------------------------------------------
// GPU rendering mode. Drives which -device we emit for the guest GPU.
// ---------------------------------------------------------------------------
enum class GpuMode {
    Virgl,        // -device virtio-gpu-gl-pci (first-class, host-GL accelerated)
    VirtioGpu,    // -device virtio-gpu-pci (software / no host GL)
    Veniam        // experimental Vulkan (Venus); emitted when we support it
};

// ---------------------------------------------------------------------------
// Where the guest display surfaces. For now we always use QEMU's own window
// (SDL/GTK) - display embedding is a Phase 2 decision and not a blocker here.
// ---------------------------------------------------------------------------
enum class DisplayBackend {
    Sdl,
    Gtk,
    Headless
};

// ---------------------------------------------------------------------------
// Per-instance configuration. Mirrors what the UI will edit. Values have
// sensible gaming defaults.
// ---------------------------------------------------------------------------
struct InstanceConfig {
    // Identity / storage
    std::string instance_id;          // short slug, e.g. "pubg1"
    std::string base_image;           // read-only qcow2 base (linked clone parent)
    std::string overlay_image;        // per-instance qcow2 overlay (child)
    int64_t     overlay_size_bytes{25LL * 1024 * 1024 * 1024}; // 25 GiB

    // Compute
    int         cpu_cores{4};
    int         cpu_threads{1};       // per core; 1 keeps SMT off for lower latency
    std::string cpu_model{"host"};    // "host" to pass through host CPU features
    int         ram_mb{4096};

    // Display
    GpuMode           gpu_mode{GpuMode::Virgl};
    DisplayBackend    display{DisplayBackend::Sdl};
    int               width{1280};
    int               height{720};
    int               dpi{240};
    int               fps_cap{0};     // 0 = uncapped
    int               gpu_mem_mb{512};
    int               max_scanlines{3}; // -device virtio-gpu max_scanouts

    // Audio
    bool            audio_enabled{true};
    std::string     audio_driver{"pipewire"}; // "pipewire" | "pulse"

    // Network (user-mode / slirp)
    int             host_adb_port{5555}; // host-forwarded -> guest:5555
    std::string     guest_ip{"10.0.2.15"};

    // QMP control socket (unix). Empty -> no QMP (Phase 1 script path).
    std::string     qmp_socket;

    // KVM
    bool            kvm_enabled{true};
};

// ---------------------------------------------------------------------------
// Builds the full QEMU argv[] (including the program name as argv[0]).
// Throws std::invalid_argument on invalid configuration.
// ---------------------------------------------------------------------------
class QemuCommandBuilder {
public:
    QemuCommandBuilder(std::string qemu_bin = "qemu-system-x86_64");

    // The complete argument vector ready for execvp().
    // `iso_path` is the Android-x86 install ISO attached as a read-only second
    // drive on first boot; pass an empty string on subsequent boots.
    std::vector<std::string> build(const InstanceConfig& cfg,
                                   const std::string& iso_path = "") const;

    // Human-readable single-line command (for logs / shell scripts / docs).
    std::string buildCommandLine(const InstanceConfig& cfg,
                                 const std::string& iso_path = "") const;

    // Individual segments (used by tests and by the shell-script boot path).
    std::vector<std::string> machine(const InstanceConfig& cfg) const;
    std::vector<std::string> memory(const InstanceConfig& cfg) const;
    std::vector<std::string> cpu(const InstanceConfig& cfg) const;
    std::vector<std::string> disk(const InstanceConfig& cfg) const;
    std::vector<std::string> bootIso(const std::string& iso_path) const;
    std::vector<std::string> gpu(const InstanceConfig& cfg) const;
    std::vector<std::string> display(const InstanceConfig& cfg) const;
    std::vector<std::string> audio(const InstanceConfig& cfg) const;
    std::vector<std::string> network(const InstanceConfig& cfg) const;
    std::vector<std::string> qmp(const InstanceConfig& cfg) const;

    const std::string& qemuBinary() const { return qemu_bin_; }
    void setQemuBinary(std::string bin) { qemu_bin_ = std::move(bin); }

private:
    void validate(const InstanceConfig& cfg) const;
    std::string shellQuote(const std::string& s) const;
    std::string join(const std::vector<std::string>& args) const;

    std::string qemu_bin_;
};

} // namespace droidforge
