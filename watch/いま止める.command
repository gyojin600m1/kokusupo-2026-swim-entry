#!/bin/zsh
cd "$(dirname "$0")"
echo "手動で止めた" > .halted
launchctl bootout "gui/$(id -u)/com.fukuda.swim.kokusupo2026-results" 2>/dev/null
echo "見張りを止めました。再開するには .halted を消して plist を bootstrap してください。"
read -k1 "?閉じるには何かキーを押してください"
