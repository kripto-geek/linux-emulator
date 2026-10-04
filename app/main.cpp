// DroidForge app - Qt 6 entry point.
//
// Phase 1: bootstrap window. Phase 3 replaces this with the gaming shell.
// Kept minimal so the build proves Qt 6 links against the core library.

#include <QApplication>
#include <QLabel>
#include <QMainWindow>
#include <QTextBrowser>

#include "droidforge/qemu_command_builder.hpp"

namespace {

class BootstrapWindow : public QMainWindow {
public:
    BootstrapWindow() {
        setWindowTitle(DROIDFORGE_DISPLAY_NAME);
        resize(720, 420);

        auto* body = new QTextBrowser(this);
        QString sample;
        sample += "<h2 style='font-size:1.2em'>DroidForge</h2>\n";
        sample += "<p>Phase 1 bootstrap. The core library built and linked.</p>\n";
        sample += "<p>Sample QEMU command the <code>core</code> library emits:</p>\n";

        droidforge::InstanceConfig cfg;
        cfg.instance_id   = "demo";
        cfg.overlay_image = "~/droidforge/demo/overlay.qcow2";
        cfg.cpu_cores     = 4;
        cfg.ram_mb        = 4096;
        cfg.gpu_mode      = droidforge::GpuMode::Virgl;
        cfg.host_adb_port = 5555;
        cfg.qmp_socket    = "/run/user/1000/droidforge/demo/qmp.sock";

        droidforge::QemuCommandBuilder b;
        QString line = QString::fromStdString(b.buildCommandLine(cfg,
            "/opt/droidforge/bliss-16.9.7.iso"));
        sample += "<pre style='background:#0e1116;color:#d7dde8;padding:8px;"
                  "border-radius:6px;font-family:monospace;'>" +
                  line.toHtmlEscaped() + "</pre>\n";
        sample += "<p>Next: Phase 1 acceptance (boot to home &lt; 60s, adb "
                  "reachable, GL renderer = NVIDIA, not SwiftShader).</p>\n";

        body->setHtml(sample);
        setCentralWidget(body);
    }
};

} // namespace

int main(int argc, char** argv) {
    QApplication app(argc, argv);
    app.setApplicationName("DroidForge");
    app.setOrganizationName("DroidForge");

    BootstrapWindow w;
    w.show();
    return app.exec();
}
