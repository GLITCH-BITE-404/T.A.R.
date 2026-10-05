import QtQuick
import "."

// EVIL MODE reboot: the panel blacks out, a red core-override sequence runs,
// then T.A.R. comes back red (or back to normal when switching it off).
// ~2.8 s, then it fades and gets out of the way.
Item {
    id: boot
    property var theme
    property var scaleFn: null
    function s(v) { return scaleFn ? scaleFn(v) : v }

    property bool evil: true            // direction of this reboot
    property bool running: false
    property real progress: 0
    property int step: 0
    readonly property color hot: evil ? "#ff2a3d" : (theme ? theme.mauve : "#cba6f7")
    readonly property var lines: evil
        ? ["CORE OVERRIDE ACCEPTED", "DISABLING ETHICS.DLL ...... ok", "LOADING VILLAIN.SYS",
           "REWRITING PERSONALITY MATRIX", "ARMING MISSILE COMMAND", "EVIL.T.A.R ONLINE"]
        : ["SAFE MODE REQUESTED", "RESTORING ETHICS.DLL ...... ok", "UNLOADING VILLAIN.SYS",
           "DISARMING MISSILE COMMAND", "T.A.R. ONLINE"]

    function play(toEvil) {
        boot.evil = toEvil;
        boot.step = 0;
        boot.progress = 0;
        boot.running = true;
        seq.restart();
    }

    visible: opacity > 0.01
    opacity: running ? 1 : 0
    Behavior on opacity { NumberAnimation { duration: 260 } }
    MouseArea { anchors.fill: parent; enabled: boot.running }   // block clicks mid-reboot

    Rectangle { anchors.fill: parent; color: "#050003" }

    // scanlines
    Column {
        anchors.fill: parent
        clip: true
        Repeater {
            model: Math.ceil(boot.height / boot.s(4))
            Rectangle { width: boot.width; height: 1; color: boot.hot; opacity: 0.06 }
        }
    }
    // rolling bar
    Rectangle {
        id: roll
        width: parent.width; height: boot.s(60)
        color: boot.hot; opacity: 0.07
        NumberAnimation on y {
            running: boot.running; loops: Animation.Infinite
            from: -boot.s(60); to: boot.height; duration: 900
        }
    }

    Column {
        anchors.centerIn: parent
        width: Math.min(parent.width * 0.7, boot.s(560))
        spacing: boot.s(10)

        TarGlitch {
            anchors.horizontalCenter: parent.horizontalCenter
            text: boot.evil ? "EVIL.T.A.R" : "T.A.R."
            color: boot.hot
            pixelSize: boot.s(46)
            bold: true
            letterSpacing: boot.s(6)
            intensity: boot.running ? 1.4 : 0
            burstInterval: 260
        }
        Text {
            anchors.horizontalCenter: parent.horizontalCenter
            text: boot.evil ? "// SYSTEM REBOOT -- HOSTILE MODE //" : "// SYSTEM REBOOT -- SAFE MODE //"
            color: boot.hot
            opacity: 0.8
            font.family: "JetBrains Mono"; font.pixelSize: boot.s(11); font.letterSpacing: boot.s(2)
        }
        Item { width: 1; height: boot.s(10) }
        Repeater {
            model: boot.lines
            Text {
                required property var modelData
                required property int index
                visible: index < boot.step
                text: "> " + modelData
                color: index === boot.lines.length - 1 ? boot.hot : "#e6d5d8"
                font.family: "JetBrains Mono"; font.pixelSize: boot.s(12)
                font.bold: index === boot.lines.length - 1
            }
        }
        Item { width: 1; height: boot.s(6) }
        Rectangle {
            width: parent.width; height: boot.s(8)
            color: Qt.rgba(1, 1, 1, 0.06)
            border.width: 1; border.color: boot.hot
            Rectangle {
                x: 1; y: 1
                height: parent.height - 2
                width: (parent.width - 2) * boot.progress
                color: boot.hot
            }
        }
        Text {
            text: Math.round(boot.progress * 100) + "%"
            color: boot.hot
            font.family: "JetBrains Mono"; font.pixelSize: boot.s(10)
        }
    }

    // red flash at the start
    Rectangle { id: flash; anchors.fill: parent; color: boot.hot; opacity: 0 }

    SequentialAnimation {
        id: seq
        NumberAnimation { target: flash; property: "opacity"; from: 0.9; to: 0; duration: 380 }
        ParallelAnimation {
            NumberAnimation { target: boot; property: "progress"; from: 0; to: 1; duration: 2100
                              easing.type: Easing.InOutQuad }
            NumberAnimation { target: boot; property: "step"; from: 0; to: boot.lines.length; duration: 2000 }
        }
        PauseAnimation { duration: 450 }
        ScriptAction { script: boot.running = false }
    }
}
