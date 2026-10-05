import QtQuick
import "."

// The input bar while T.A.R. is hearing you (voice note OR auto-listen):
// pulsing dot + timer/label + a scrolling waveform, newest on the right.
// Same strip in chat and orb mode.
Item {
    id: strip
    property var theme
    property color accent: "#cba6f7"
    property var scaleFn: null
    function s(v) { return scaleFn ? scaleFn(v) : v }

    property var levels: []
    property bool busy: false          // transcribing
    property string label: ""          // "0:04" / "listening" / "transcribing…"

    Row {
        id: head
        anchors.left: parent.left
        anchors.verticalCenter: parent.verticalCenter
        spacing: strip.s(8)
        Rectangle {
            width: strip.s(8); height: width; radius: width / 2
            anchors.verticalCenter: parent.verticalCenter
            color: strip.busy ? strip.accent : strip.theme.red
            SequentialAnimation on opacity {
                loops: Animation.Infinite; running: strip.visible
                NumberAnimation { to: 0.2; duration: 500 }
                NumberAnimation { to: 1; duration: 500 }
            }
        }
        Text {
            anchors.verticalCenter: parent.verticalCenter
            text: strip.label
            color: strip.theme.text
            font.family: "JetBrains Mono"
            font.pixelSize: strip.s(12)
        }
    }
    Item {
        id: wave
        anchors.left: head.right
        anchors.leftMargin: strip.s(10)
        anchors.right: parent.right
        anchors.top: parent.top
        anchors.bottom: parent.bottom
        clip: true
        readonly property real step: strip.s(4)
        readonly property int n: Math.max(1, Math.floor(width / step))
        Row {
            anchors.right: parent.right
            anchors.verticalCenter: parent.verticalCenter
            spacing: wave.step - strip.s(2)
            Repeater {
                model: wave.n
                Rectangle {
                    readonly property real v: {
                        var L = strip.levels, i = L.length - wave.n + index;
                        return i >= 0 && i < L.length ? L[i] : 0;
                    }
                    width: strip.s(2)
                    height: Math.max(strip.s(2), wave.height * 0.8 * v)
                    radius: width / 2
                    anchors.verticalCenter: parent.verticalCenter
                    color: strip.busy ? strip.theme.overlay1 : strip.accent
                    opacity: v > 0 ? 0.55 + 0.45 * v : 0.25
                }
            }
        }
    }
}
