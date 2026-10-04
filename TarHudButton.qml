import QtQuick

// Bracketed HUD toggle. Glyph over a tiny caps label, corner ticks that
// light up on hover, accent fill when active.
Item {
    id: btn

    property var theme
    property color accent: "#cba6f7"
    property var scaleFn: null
    function s(v) { return scaleFn ? scaleFn(v) : v }

    property string glyph: ""
    property string label: ""
    property bool active: false

    // A control for a capability that isn't installed should LOOK dead, not
    // pretend to work. `locked` greys it out and routes the click somewhere
    // useful instead of firing an action that can't happen.
    property bool locked: false
    property string lockedHint: ""

    signal clicked()
    signal lockedClicked()

    implicitWidth: Math.max(s(44), lbl.implicitWidth + s(14))
    implicitHeight: s(30)
    opacity: btn.locked ? 0.42 : 1.0
    Behavior on opacity { NumberAnimation { duration: 140 } }

    Rectangle {
        anchors.fill: parent
        color: btn.active
            ? Qt.rgba(btn.accent.r, btn.accent.g, btn.accent.b, 0.16)
            : Qt.rgba(btn.theme.mantle.r, btn.theme.mantle.g, btn.theme.mantle.b, 0.5)
        border.width: 1
        border.color: btn.active
            ? Qt.rgba(btn.accent.r, btn.accent.g, btn.accent.b, 0.6)
            : Qt.rgba(btn.theme.surface1.r, btn.theme.surface1.g, btn.theme.surface1.b, 0.5)
        Behavior on color { ColorAnimation { duration: 140 } }
        Behavior on border.color { ColorAnimation { duration: 140 } }
    }

    // corner ticks, brighter on hover/active
    Repeater {
        model: 4
        Item {
            readonly property bool rightSide: index === 1 || index === 2
            readonly property bool bottomSide: index >= 2
            width: btn.s(5); height: btn.s(5)
            x: rightSide ? btn.width - width : 0
            y: bottomSide ? btn.height - height : 0
            opacity: (hover.hovered || btn.active) ? 1.0 : 0.35
            Behavior on opacity { NumberAnimation { duration: 140 } }

            Rectangle {
                width: parent.width; height: 1.5; color: btn.accent
                y: parent.bottomSide ? parent.height - height : 0
            }
            Rectangle {
                width: 1.5; height: parent.height; color: btn.accent
                x: parent.rightSide ? parent.width - width : 0
            }
        }
    }

    Column {
        anchors.centerIn: parent
        spacing: 0

        Text {
            anchors.horizontalCenter: parent.horizontalCenter
            text: btn.glyph
            color: btn.active ? btn.accent : btn.theme.subtext0
            font.family: "Iosevka Nerd Font"
            font.pixelSize: btn.s(11)
        }
        Text {
            id: lbl
            anchors.horizontalCenter: parent.horizontalCenter
            text: btn.label
            color: btn.active ? btn.accent : btn.theme.overlay0
            font.family: "JetBrains Mono"
            font.pixelSize: btn.s(6)
            font.letterSpacing: btn.s(1)
        }
    }

    HoverHandler { id: hover }
    TapHandler {
        onTapped: btn.locked ? btn.lockedClicked() : btn.clicked()
    }
}
