// T.A.R. screen effect host:  TAR_FX=<name|/abs/path.qml> TAR_FX_SECS=<n> qs -p fx/host.qml
// One click-through overlay over every screen showing the effect, gone after
// TAR_FX_SECS. tar_tools.py `screen_fx` / `make_fx` start it, `stop_fx` kills it.
// Prints "FX_READY" once the effect loaded and "FX_FAIL" if it didn't, so
// T.A.R. can tell the model the real result (and its errors) instead of guessing.
import Quickshell
import Quickshell.Wayland
import QtQuick

ShellRoot {
    id: root
    readonly property string fx: Quickshell.env("TAR_FX") || "matrix"
    readonly property int secs: parseInt(Quickshell.env("TAR_FX_SECS") || "30")
    readonly property string shots: Quickshell.env("TAR_FX_SHOTS") || "/tmp"
    // effects built from a screenshot (shake) must appear instantly
    readonly property int fadeMs: parseInt(Quickshell.env("TAR_FX_FADE") || "600")

    Variants {
        model: Quickshell.screens
        PanelWindow {
            required property var modelData
            screen: modelData
            anchors { top: true; bottom: true; left: true; right: true }
            exclusionMode: ExclusionMode.Ignore
            color: "transparent"
            WlrLayershell.layer: WlrLayer.Overlay
            WlrLayershell.keyboardFocus: WlrKeyboardFocus.None
            WlrLayershell.namespace: "tar-fx"
            mask: Region {}                 // clicks pass straight through

            Loader {
                id: effect
                anchors.fill: parent
                source: root.fx.charAt(0) === "/" ? "file://" + root.fx : root.fx + ".qml"
                opacity: root.fadeMs > 0 ? 0 : 1
                onStatusChanged: {
                    if (status === Loader.Ready) {
                        // effects that need a picture of this screen get one
                        if (item.hasOwnProperty("shot"))
                            item.shot = root.shots + "/fx-" + modelData.name + ".png";
                        console.log("FX_READY");
                        if (root.fadeMs > 0) fadeIn.start();
                    } else if (status === Loader.Error) {
                        console.log("FX_FAIL");
                        Qt.exit(3);
                    }
                }
                NumberAnimation { id: fadeIn; target: effect; property: "opacity"; to: 1
                                  duration: Math.max(1, root.fadeMs) }
                NumberAnimation { id: fadeOut; target: effect; property: "opacity"; to: 0
                                  duration: Math.max(150, Math.min(900, root.fadeMs * 1.5))
                                  onFinished: Qt.quit() }
            }
            Timer { interval: Math.max(1500, root.secs * 1000 - fadeOut.duration); running: true
                    onTriggered: fadeOut.start() }
        }
    }
}
