#pragma once

// DroidForge core – thin ADB wrapper.
//
// Wraps the `adb` CLI for operations DroidForge needs:
//   * connect / disconnect to a TCP device (ADB over TCP, our QEMU guest)
//   * push a file to the guest
//   * forward / reverse-forward a port
//   * shell command (with stdout capture)
//   * install an APK
//   * get a property
//   * push the scrcpy-server JAR and start/stop the control server
//
// This is a thin wrapper around fork/exec of `adb`. It does NOT open the ADB
// protocol directly; that would require maintaining compatibility with the ADB
// wire protocol. The `adb` binary is always available on a DroidForge install
// (android-tools package).
//
// Thread safety: each call is synchronous and blocks. The caller is
// responsible for not calling the same AdbClient from multiple threads
// concurrently (the QMP/touch engine runs on separate threads).

#include <cstdint>
#include <string>
#include <vector>
#include <chrono>

namespace droidforge {

struct AdbResult {
    int         exit_code{0};
    std::string stdout_data;
    std::string stderr_data;
    bool ok() const { return exit_code == 0; }
};

class AdbClient {
public:
    // device_serial: "127.0.0.1:5555" for a TCP ADB device.
    explicit AdbClient(std::string device_serial,
                       std::string adb_binary = "adb");

    // ---------- connection ----------

    // Connect the ADB server to the TCP device. Returns true if the device
    // appears in `adb devices` within timeout.
    bool connect(std::chrono::seconds timeout = std::chrono::seconds(60));
    void disconnect();

    // Wait until `adb shell echo ping` succeeds (boot complete).
    bool waitForDevice(std::chrono::seconds timeout = std::chrono::seconds(90));

    // ---------- port forwarding ----------

    // Forward host:tcp:host_port → device:tcp:guest_port.
    // Used to reach the scrcpy-server control socket.
    AdbResult forward(uint16_t host_port, uint16_t guest_port);
    AdbResult forwardLocalAbstract(uint16_t host_port,
                                   const std::string& abstract_socket_name);
    AdbResult removeForward(uint16_t host_port);

    // ---------- file operations ----------

    AdbResult push(const std::string& local_path,
                   const std::string& remote_path);
    AdbResult pull(const std::string& remote_path,
                   const std::string& local_path);

    // ---------- shell ----------

    // Run a shell command and return its output. Throws on exec failure.
    AdbResult shell(const std::string& command,
                    std::chrono::seconds timeout = std::chrono::seconds(30));

    // Like shell() but ignores exit code.
    std::string shellOutput(const std::string& command,
                            std::chrono::seconds timeout = std::chrono::seconds(10));

    // ---------- app management ----------

    // Install an APK. Returns true on success.
    AdbResult installApk(const std::string& apk_path,
                         bool replace = true,
                         bool downgrade = false);

    // ---------- properties ----------

    // Read a single Android system property.
    std::string getprop(const std::string& key);

    // ---------- scrcpy server ----------

    // Push the scrcpy-server JAR from tools_dir and start it on the device.
    // Returns the host TCP port the control socket is forwarded to.
    // The scrcpy server uses localabstract socket "scrcpy" which we forward
    // to a host TCP port for the TouchInjector.
    //
    // server_version: the scrcpy protocol version string (e.g. "4.1"),
    //   must match the bundled scrcpy-server.jar.
    // host_control_port: host-side TCP port (e.g. 27183).
    bool startScrcpyServer(const std::string& tools_dir,
                           const std::string& server_version,
                           uint16_t host_control_port,
                           int32_t screen_w, int32_t screen_h);

    void stopScrcpyServer();

    const std::string& serial() const { return device_serial_; }

private:
    // Run: adb [-s serial] <args...>
    AdbResult run(const std::vector<std::string>& args,
                  std::chrono::seconds timeout = std::chrono::seconds(30));

    std::string device_serial_;
    std::string adb_binary_;
    pid_t       scrcpy_server_pid_{-1};
};

} // namespace droidforge
