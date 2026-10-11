// wfmix PC-Stub fuer org.asteroid.controls Icon (zeigt Platzhalter)
import QtQuick 2.9
Item {
    property string name: ""
    property color color: "white"
    Rectangle { anchors.fill: parent; color: "transparent"; border.color: parent.color; border.width: 1; radius: width / 2; opacity: 0.6 }
    Text { anchors.centerIn: parent; color: parent.color; font.pixelSize: parent.height * 0.3; text: name.replace("ios-", "").slice(0, 4) }
}
