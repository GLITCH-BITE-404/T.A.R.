import QtQuick

// One block in the console: bracketed title, optional note, content slot.
Item {
    id: sec

    property var theme
    property color accent: "#cba6f7"
    property var scaleFn: null
    function s(v) { return scaleFn ? scaleFn(v) : v }

    property string title: ""
    property string note: ""

    default property alias content: slot.data

    implicitHeight: col.implicitHeight

    Column {
        id: col
        width: sec.width
        spacing: sec.s(7)

        Row {
            spacing: sec.s(7)
            Rectangle {
                width: sec.s(3); height: sec.s(11)
                color: sec.accent
                anchors.verticalCenter: parent.verticalCenter
            }
            Text {
                text: sec.title
                color: sec.theme.text
                font.family: "JetBrains Mono"
                font.pixelSize: sec.s(10)
                font.bold: true
                font.letterSpacing: sec.s(2)
                anchors.verticalCenter: parent.verticalCenter
            }
            Text {
                text: sec.note
                color: sec.theme.overlay0
                font.family: "JetBrains Mono"
                font.pixelSize: sec.s(8)
                anchors.verticalCenter: parent.verticalCenter
                visible: sec.note !== ""
            }
        }

        Item {
            id: slot
            width: col.width
            implicitHeight: childrenRect.height
            height: childrenRect.height
        }
    }
}
