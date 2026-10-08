// T.A.R. screen effect host:  TAR_FX=<name> TAR_FX_SECS=<n> qs -p fx/host.qml
// One click-through overlay over every screen, showing fx/<name>.qml, gone
// after TAR_FX_SECS (tar_tools.py `screen_fx` starts it, `stop_fx` kills it).
import Quickshell
import Quickshell.Wayland
import QtQuick

ShellRoot {
    id: root
    readonly property string fx: Quickshell.env("TAR_FX") || "matrix"
    readonly property int secs: parseInt(Quickshell.env("TAR_FX_SECS") || "30")

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
                source: root.fx + ".qml"
                opacity: 0
                Component.onCompleted: fadeIn.start()
                NumberAnimation { id: fadeIn; target: effect; property: "opacity"; to: 1; duration: 600 }
                NumberAnimation { id: fadeOut; target: effect; property: "opacity"; to: 0; duration: 900
                                  onFinished: Qt.quit() }
            }
            Timer { interval: Math.max(2, root.secs) * 1000 - 900; running: true
                    onTriggered: fadeOut.start() }
        }
    }
}
