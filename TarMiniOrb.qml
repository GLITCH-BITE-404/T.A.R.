// T.A.R.'s mini orb: shown while T.A.R. works on the screen (clicking, typing,
// playing). It rests at a corner of the window being worked on, glides to just
// BESIDE each spot before it's clicked, flashes a ring on the click, and says
// what it's doing in a speech bubble.
//
// Driven by $TAR_DATA/mini.json, which tar_tools.py writes (so the chat brain
// AND background tasks move it). Python owns the layout -- the same boxes are
// blacked out of T.A.R.'s screenshots, so it never sees or clicks itself.
//
// Click-through: the overlay's input mask is EMPTY, so every click lands on the
// app underneath. It only exists while active (LazyLoader), so idle costs nothing.
import Quickshell
import Quickshell.Io
import Quickshell.Wayland
import QtQuick

Scope {
    id: mini

    readonly property string dataDir: Quickshell.env("TAR_DATA")
        || (Quickshell.env("HOME") + "/.local/share/bite-os/tar")
    property var st: ({})
    property bool on: false
    property int lastSeq: -1
    property int lastClick: -1
    property bool flash: false

    MatugenColors { id: theme }

    function screenFor(name) {
        var all = Quickshell.screens;
        for (var i = 0; i < all.length; i++)
            if (all[i].name === name) return all[i];
        return all.length ? all[0] : null;
    }

    FileView {
        id: feed
        path: mini.dataDir + "/mini.json"
        printErrors: false
        onLoaded: {
            try {
                var d = JSON.parse(text());
                if (d.seq === mini.lastSeq) return;
                mini.lastSeq = d.seq;
                var fresh = (Date.now() / 1000 - (d.at || 0)) < 6;
                if (d.click !== undefined && d.click !== mini.lastClick) {
                    if (mini.lastClick >= 0 && fresh) mini.flash = !mini.flash;
                    mini.lastClick = d.click;
                }
                mini.st = d;
                mini.on = !!d.on && fresh;
                if (mini.on) idle.restart();
            } catch (e) {}
        }
    }
    // a rename-replaced file can drop an inotify watch, so poll: fast while
    // the orb is up, slow while idle (a tiny local file read)
    Timer {
        interval: mini.on ? 70 : 400
        running: true
        repeat: true
        onTriggered: feed.reload()
    }
    Timer { id: idle; interval: 6000; onTriggered: mini.on = false }

    LazyLoader {
        active: mini.on

        PanelWindow {
            id: panel
            screen: mini.screenFor(mini.st.mon)
            anchors { top: true; bottom: true; left: true; right: true }
            exclusionMode: ExclusionMode.Ignore
            color: "transparent"
            WlrLayershell.layer: WlrLayer.Overlay
            WlrLayershell.keyboardFocus: WlrKeyboardFocus.None
            WlrLayershell.namespace: "tar-mini"
            mask: Region {}                 // empty input region = clicks pass through

            readonly property color accent: theme.mauve
            readonly property bool thinking: (mini.st.say || "").length > 0

            // click flash at the real target spot
            Rectangle {
                id: ring
                property real k: 0
                x: (mini.st.tx || 0) - width / 2
                y: (mini.st.ty || 0) - height / 2
                width: 14 + 46 * k; height: width; radius: width / 2
                color: "transparent"
                border.width: 2
                border.color: panel.accent
                opacity: 1 - k
                visible: k > 0 && k < 1
                NumberAnimation on k { id: ringAnim; from: 0; to: 1; duration: 420; running: false
                                       easing.type: Easing.OutCubic }
                Connections {
                    target: mini
                    function onFlashChanged() { ringAnim.restart(); }
                }
            }

            Item {
                id: orb
                visible: mini.st.x !== undefined
                width: 40; height: 40
                x: (mini.st.x || 0) - width / 2
                y: (mini.st.y || 0) - height / 2
                Behavior on x { NumberAnimation { duration: 300; easing.type: Easing.OutCubic } }
                Behavior on y { NumberAnimation { duration: 300; easing.type: Easing.OutCubic } }

                // glow
                Rectangle {
                    anchors.centerIn: parent
                    width: 50; height: 50; radius: 25
                    color: panel.accent
                    opacity: 0.18 + 0.1 * pulse.k
                }
                // spinning arc ring
                Rectangle {
                    anchors.centerIn: parent
                    width: 38; height: 38; radius: 19
                    color: "transparent"
                    border.width: 2
                    border.color: Qt.rgba(panel.accent.r, panel.accent.g, panel.accent.b, 0.55)
                    Rectangle {
                        width: 8; height: 8; radius: 4
                        color: theme.text
                        x: parent.width / 2 - 4; y: -3
                    }
                    RotationAnimation on rotation {
                        from: 0; to: 360; duration: panel.thinking ? 1400 : 3200
                        loops: Animation.Infinite; running: mini.on
                    }
                }
                // core
                Rectangle {
                    id: core
                    anchors.centerIn: parent
                    width: 20 + 4 * pulse.k; height: width; radius: width / 2
                    color: panel.accent
                    border.width: 1
                    border.color: theme.text
                }
                Item {
                    id: pulse
                    visible: false
                    property real k: 0
                    SequentialAnimation on k {
                        loops: Animation.Infinite; running: mini.on
                        NumberAnimation { to: 1; duration: 700; easing.type: Easing.InOutSine }
                        NumberAnimation { to: 0; duration: 700; easing.type: Easing.InOutSine }
                    }
                }
            }

            // cartoon speech bubble (box decided by Python; text fits inside it)
            Item {
                id: bubble
                visible: panel.thinking
                x: mini.st.bx || 0
                y: mini.st.by || 0
                width: 264; height: 72
                readonly property bool below: (mini.st.by || 0) > (mini.st.y || 0)
                Behavior on x { NumberAnimation { duration: 300; easing.type: Easing.OutCubic } }
                Behavior on y { NumberAnimation { duration: 300; easing.type: Easing.OutCubic } }

                Rectangle {
                    id: box
                    width: Math.min(264, said.implicitWidth + 24)
                    height: Math.min(72, said.implicitHeight + 16)
                    x: Math.max(0, Math.min(264 - width, (mini.st.x || 0) - bubble.x - width / 2))
                    y: bubble.below ? 0 : 72 - height
                    radius: 12
                    color: Qt.rgba(theme.base.r, theme.base.g, theme.base.b, 0.94)
                    border.width: 1
                    border.color: panel.accent
                    Text {
                        id: said
                        anchors.centerIn: parent
                        width: Math.min(implicitWidth, 240)
                        text: mini.st.say || ""
                        color: theme.text
                        font.family: "JetBrains Mono"
                        font.pixelSize: 11
                        wrapMode: Text.Wrap
                        maximumLineCount: 3
                        elide: Text.ElideRight
                    }
                }
                // tail pointing at the orb
                Rectangle {
                    width: 10; height: 10
                    rotation: 45
                    color: box.color
                    border.width: 1
                    border.color: panel.accent
                    x: Math.max(box.x + 10, Math.min(box.x + box.width - 20,
                                                     (mini.st.x || 0) - bubble.x - 5))
                    y: bubble.below ? -5 : 72 - 5
                    z: -1
                }
            }
        }
    }
}
