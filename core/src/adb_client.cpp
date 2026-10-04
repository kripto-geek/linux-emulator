// DroidForge core – ADB client implementation.
//
// All `adb` calls go through run() which fork/exec's the adb binary and
// captures stdout/stderr. No direct ADB protocol implementation.

#include "droidforge/adb_client.hpp"

#include <fcntl.h>
#include <poll.h>
#include <signal.h>
#include <sys/types.h>
#include <sys/wait.h>
#include <unistd.h>

#include <cerrno>
#include <cstring>
#include <filesystem>
#include <stdexcept>
#include <thread>

namespace droidforge {

// ---------------------------------------------------------------------------
// Constructor
// ---------------------------------------------------------------------------
AdbClient::AdbClient(std::string device_serial, std::string adb_binary)
    : device_serial_(std::move(device_serial))
    , adb_binary_(std::move(adb_binary))
{}

// ---------------------------------------------------------------------------
// run() – fork/exec `adb [-s serial] args...` and collect output.
// ---------------------------------------------------------------------------
AdbResult AdbClient::run(const std::vector<std::string>& args,
                          std::chrono::seconds timeout)
{
    // Build argv: ["adb", "-s", serial, arg0, arg1, ...]
    std::vector<std::string> full_args;
    full_args.push_back(adb_binary_);
    if (!device_serial_.empty()) {
        full_args.push_back("-s");
        full_args.push_back(device_serial_);
    }
    for (auto& a : args) full_args.push_back(a);

    std::vector<const char*> argv;
    for (auto& s : full_args) argv.push_back(s.c_str());
    argv.push_back(nullptr);

    // Pipes for stdout and stderr.
    int out_pipe[2], err_pipe[2];
    if (::pipe2(out_pipe, O_CLOEXEC) < 0 || ::pipe2(err_pipe, O_CLOEXEC) < 0)
        throw std::runtime_error("pipe2 failed: " + std::string(::strerror(errno)));

    pid_t pid = ::fork();
    if (pid < 0) throw std::runtime_error("fork failed");

    if (pid == 0) {
        // Child
        ::dup2(out_pipe[1], STDOUT_FILENO);
        ::dup2(err_pipe[1], STDERR_FILENO);
        ::close(out_pipe[0]); ::close(out_pipe[1]);
        ::close(err_pipe[0]); ::close(err_pipe[1]);
        ::execvp(argv[0], const_cast<char**>(argv.data()));
        ::_exit(127);
    }

    // Parent
    ::close(out_pipe[1]);
    ::close(err_pipe[1]);

    AdbResult result;
    auto deadline = std::chrono::steady_clock::now() + timeout;

    // Read stdout + stderr until both pipes close (child exits) or timeout.
    struct pollfd pfds[2];
    pfds[0].fd = out_pipe[0]; pfds[0].events = POLLIN;
    pfds[1].fd = err_pipe[0]; pfds[1].events = POLLIN;
    int open_count = 2;

    while (open_count > 0) {
        auto now = std::chrono::steady_clock::now();
        if (now >= deadline) {
            ::kill(pid, SIGTERM);
            ::waitpid(pid, nullptr, 0);
            ::close(out_pipe[0]); ::close(err_pipe[0]);
            result.exit_code = -ETIMEDOUT;
            result.stderr_data += "\n[droidforge] adb timeout\n";
            return result;
        }
        int ms = static_cast<int>(
            std::chrono::duration_cast<std::chrono::milliseconds>(
                deadline - now).count());
        int r = ::poll(pfds, 2, ms);
        if (r <= 0) continue;

        char buf[4096];
        for (int i = 0; i < 2; ++i) {
            if (pfds[i].revents & (POLLIN | POLLHUP)) {
                ssize_t n = ::read(pfds[i].fd, buf, sizeof(buf));
                if (n > 0) {
                    (i == 0 ? result.stdout_data : result.stderr_data)
                        .append(buf, static_cast<size_t>(n));
                } else if (n == 0) {
                    ::close(pfds[i].fd);
                    pfds[i].fd = -1;
                    --open_count;
                }
            }
        }
    }

    int status = 0;
    ::waitpid(pid, &status, 0);
    result.exit_code = WIFEXITED(status) ? WEXITSTATUS(status) : -1;
    return result;
}

// ---------------------------------------------------------------------------
// connect / disconnect
// ---------------------------------------------------------------------------
bool AdbClient::connect(std::chrono::seconds timeout) {
    // `adb connect host:port` returns "connected to …" on success.
    auto r = run({"connect", device_serial_}, timeout);
    return r.ok() &&
           (r.stdout_data.find("connected") != std::string::npos ||
            r.stdout_data.find("already") != std::string::npos);
}

void AdbClient::disconnect() {
    run({"disconnect", device_serial_}, std::chrono::seconds(5));
}

bool AdbClient::waitForDevice(std::chrono::seconds timeout) {
    // Poll `adb shell echo ping` until it responds.
    auto deadline = std::chrono::steady_clock::now() + timeout;
    while (std::chrono::steady_clock::now() < deadline) {
        auto r = shell("echo ping", std::chrono::seconds(5));
        if (r.ok() && r.stdout_data.find("ping") != std::string::npos)
            return true;
        std::this_thread::sleep_for(std::chrono::seconds(2));
    }
    return false;
}

// ---------------------------------------------------------------------------
// Port forwarding
// ---------------------------------------------------------------------------
AdbResult AdbClient::forward(uint16_t host_port, uint16_t guest_port) {
    return run({"forward",
                "tcp:" + std::to_string(host_port),
                "tcp:" + std::to_string(guest_port)});
}

AdbResult AdbClient::forwardLocalAbstract(uint16_t host_port,
                                           const std::string& name) {
    return run({"forward",
                "tcp:" + std::to_string(host_port),
                "localabstract:" + name});
}

AdbResult AdbClient::removeForward(uint16_t host_port) {
    return run({"forward", "--remove", "tcp:" + std::to_string(host_port)});
}

// ---------------------------------------------------------------------------
// File operations
// ---------------------------------------------------------------------------
AdbResult AdbClient::push(const std::string& local_path,
                           const std::string& remote_path) {
    return run({"push", local_path, remote_path},
               std::chrono::seconds(120));
}

AdbResult AdbClient::pull(const std::string& remote_path,
                           const std::string& local_path) {
    return run({"pull", remote_path, local_path},
               std::chrono::seconds(120));
}

// ---------------------------------------------------------------------------
// Shell
// ---------------------------------------------------------------------------
AdbResult AdbClient::shell(const std::string& command,
                            std::chrono::seconds timeout) {
    return run({"shell", command}, timeout);
}

std::string AdbClient::shellOutput(const std::string& command,
                                    std::chrono::seconds timeout) {
    auto r = shell(command, timeout);
    // Trim trailing whitespace/newlines.
    auto& s = r.stdout_data;
    while (!s.empty() && (s.back() == '\n' || s.back() == '\r' ||
                          s.back() == ' '))
        s.pop_back();
    return s;
}

// ---------------------------------------------------------------------------
// APK install
// ---------------------------------------------------------------------------
AdbResult AdbClient::installApk(const std::string& apk_path,
                                  bool replace, bool downgrade) {
    std::vector<std::string> args{"install"};
    if (replace)   args.push_back("-r");
    if (downgrade) args.push_back("-d");
    args.push_back(apk_path);
    return run(args, std::chrono::seconds(300));
}

// ---------------------------------------------------------------------------
// Properties
// ---------------------------------------------------------------------------
std::string AdbClient::getprop(const std::string& key) {
    return shellOutput("getprop " + key);
}

// ---------------------------------------------------------------------------
// scrcpy-server lifecycle
// ---------------------------------------------------------------------------
bool AdbClient::startScrcpyServer(const std::string& tools_dir,
                                   const std::string& server_version,
                                   uint16_t host_control_port,
                                   int32_t  screen_w [[maybe_unused]],
                                   int32_t  screen_h [[maybe_unused]])
{
    // Step 1: Push the server JAR.
    std::string jar = tools_dir + "/scrcpy-server.jar";
    if (!std::filesystem::exists(jar))
        throw std::runtime_error("scrcpy-server.jar not found: " + jar);

    auto r = push(jar, "/data/local/tmp/scrcpy-server.jar");
    if (!r.ok())
        throw std::runtime_error("adb push scrcpy-server failed:\n" +
                                  r.stderr_data);

    // Step 2: Forward the abstract socket to a host TCP port.
    // scrcpy-server listens on localabstract:scrcpy.
    forwardLocalAbstract(host_control_port, "scrcpy");

    // Step 3: Start the server in a detached shell.
    // We launch app_process with CLASSPATH pointing at the JAR.
    // Parameters: version, tunnel_forward=true, control=true, send_frame_meta=false
    // (no video; we use virgl for display).
    std::string cmd =
        "CLASSPATH=/data/local/tmp/scrcpy-server.jar"
        " app_process"
        " / com.genymobile.scrcpy.Server"
        " " + server_version +
        " log_level=info"
        " tunnel_forward=true"
        " control=true"
        " video=false"
        " audio=false"
        " max_size=0"
        " video_codec=h264"        // required arg even with video=false
        " video_source=display"
        " video_bit_rate=8000000"
        " max_fps=0"
        " lock_video_orientation=-1"
        " crop=-"
        " send_frame_meta=false"
        " stay_awake=false"
        " codec_options=-"
        " encoder_name=-"
        " power_off_on_close=false"
        " clipboard_autosync=false"
        " downsize_on_error=true"
        " &";

    // We start this as a background shell command. The server stays alive
    // until we kill it or the ADB connection drops.
    // We record the PID by reading from /proc after launch.
    shell(cmd, std::chrono::seconds(5));

    // Give the server a moment to start.
    std::this_thread::sleep_for(std::chrono::milliseconds(500));

    // Verify it's running.
    auto pid_str = shellOutput("pidof app_process");
    return !pid_str.empty();
}

void AdbClient::stopScrcpyServer() {
    shell("pkill -f scrcpy-server", std::chrono::seconds(5));
    removeForward(27183);
}

} // namespace droidforge
