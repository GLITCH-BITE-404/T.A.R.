import QtQuick

// Small bracketed toggle pill used across the console.
Item {
    id: chip

    property var theme
    property color accent: "#cba6f7"
    property var scaleFn: null
    function s(v) { return scaleFn ? scaleFn(v) : v }

    property string text: ""
    property bool active: false

    signal tapped()

    implicitWidth: lbl.implicitWidth + s(18)
    implicitHeight: s(24)

    Rectangle {
        anchors.fill: parent
        color: chip.active
            ? Qt.rgba(chip.accent.r, chip.accent.g, chip.accent.b, 0.18)
            : (hov.hovered
               ? Qt.rgba(chip.theme.surface0.r, chip.theme.surface0.g,
                         chip.theme.surface0.b, 0.6)
               : "transparent")
        border.width: 1
        border.color: chip.active
            ? Qt.rgba(chip.accent.r, chip.accent.g, chip.accent.b, 0.7)
            : Qt.rgba(chip.theme.surface1.r, chip.theme.surface1.g,
                      chip.theme.surface1.b, 0.6)
        Behavior on color { ColorAnimation { duration: 120 } }
        Behavior on border.color { ColorAnimation { duration: 120 } }
    }

    Text {
        id: lbl
        anchors.centerIn: parent
        text: chip.text
        color: chip.active ? chip.accent : chip.theme.overlay1
        font.family: "JetBrains Mono"
        font.pixelSize: chip.s(9)
        font.letterSpacing: chip.s(1)
    }

    HoverHandler { id: hov }
    TapHandler { onTapped: chip.tapped() }
}
