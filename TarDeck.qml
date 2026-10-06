import QtQuick
import "."

// Shared chrome for T.A.R.'s side drawers (viewer, tasks): one look, one set
// of sizes. A header with glyph + title + a live status line and a real close
// button, a hairline, then the drawer's content in `body`.
Item {
    id: deck
    signal closed()

    property var theme
    property color accent: "#cba6f7"
    property var scaleFn: null
    function s(v) { return scaleFn ? scaleFn(v) : v }

    property bool open: false
    property string glyph: ""
    property string title: ""
    property string status: ""
    property color statusColor: theme ? theme.subtext0 : "white"
    property bool statusPulse: false
    property bool solid: false          // covering the chat (narrow window): no see-through
    default property alias content: body.data

    visible: opacity > 0.01
    opacity: open ? 1 : 0
    transform: Translate {
        x: deck.open ? 0 : deck.width + deck.s(14)
        Behavior on x { NumberAnimation { duration: 340; easing.type: Easing.OutExpo } }
    }
    Behavior on opacity { NumberAnimation { duration: 200 } }

    // eat clicks so they never fall through to the chat behind
    MouseArea { anchors.fill: parent; hoverEnabled: true }

    Rectangle {
        anchors.fill: parent
        radius: deck.s(6)
        color: Qt.rgba(deck.theme.crust.r, deck.theme.crust.g, deck.theme.crust.b, deck.solid ? 1 : 0.96)
        border.width: 1
        border.color: Qt.rgba(deck.accent.r, deck.accent.g, deck.accent.b, 0.35)
    }

    // ---- header
    Item {
        id: header
        anchors.top: parent.top
        anchors.left: parent.left
        anchors.right: parent.right
        anchors.margins: deck.s(14)
        height: deck.s(44)

        Text {
            id: g
            anchors.left: parent.left
            anchors.verticalCenter: parent.verticalCenter
            text: deck.glyph
            color: deck.accent
            font.family: "JetBrains Mono"
            font.pixelSize: deck.s(20)
        }
        Column {
            anchors.left: g.right
            anchors.leftMargin: deck.s(10)
            anchors.right: closeBtn.left
            anchors.rightMargin: deck.s(8)
            anchors.verticalCenter: parent.verticalCenter
            spacing: deck.s(2)
            Text {
                text: deck.title
                color: deck.theme.text
                font.family: "JetBrains Mono"
                font.pixelSize: deck.s(13)
                font.bold: true
                font.letterSpacing: deck.s(2)
            }
            Row {
                spacing: deck.s(6)
                Rectangle {
                    visible: deck.status !== ""
                    width: deck.s(7); height: width; radius: width / 2
                    anchors.verticalCenter: parent.verticalCenter
                    color: deck.statusColor
                    SequentialAnimation on opacity {
                        loops: Animation.Infinite
                        running: deck.open && deck.statusPulse
                        NumberAnimation { to: 0.25; duration: 650 }
                        NumberAnimation { to: 1.0; duration: 650 }
                    }
                }
                Text {
                    text: deck.status
                    color: deck.statusColor
                    font.family: "JetBrains Mono"
                    font.pixelSize: deck.s(10)
                    elide: Text.ElideRight
                }
            }
        }
        TarHudButton {
            id: closeBtn
            anchors.right: parent.right
            anchors.verticalCenter: parent.verticalCenter
            theme: deck.theme; accent: deck.accent; scaleFn: deck.scaleFn
            glyph: "\u{f0156}"; label: "CLOSE"
            onClicked: deck.closed()
        }
    }
    Rectangle {
        id: rule
        anchors.top: header.bottom
        anchors.topMargin: deck.s(8)
        anchors.left: parent.left
        anchors.right: parent.right
        anchors.leftMargin: deck.s(14)
        anchors.rightMargin: deck.s(14)
        height: 1
        color: Qt.rgba(deck.theme.surface1.r, deck.theme.surface1.g, deck.theme.surface1.b, 0.7)
    }

    Item {
        id: body
        anchors.top: rule.bottom
        anchors.topMargin: deck.s(12)
        anchors.left: parent.left
        anchors.right: parent.right
        anchors.bottom: parent.bottom
        anchors.leftMargin: deck.s(14)
        anchors.rightMargin: deck.s(14)
        anchors.bottomMargin: deck.s(14)
    }
}
