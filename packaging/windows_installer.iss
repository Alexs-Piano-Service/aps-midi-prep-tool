; Package the already-tested, signed standalone payload. Do not freeze a second
; dependency graph for the installer. ISCC /DAppVersion=... /DPayload=... /O...
#ifndef AppVersion
  #error AppVersion must match packaging/release.json
#endif
#ifndef Payload
  #error Payload must identify the signed APSMIDIPrepTool.exe
#endif

[Setup]
AppId={{B73C26E6-CC35-4F1C-AC44-5FC3F7A8E8C1}
AppName=APS MIDI Prep Tool
AppVersion={#AppVersion}
AppPublisher=Alex's Piano Service LLC
AppPublisherURL=https://www.alexanderpeppe.com/
DefaultDirName={autopf}\APS MIDI Prep Tool
DefaultGroupName=APS MIDI Prep Tool
UninstallDisplayIcon={app}\APS MIDI Prep Tool.exe
OutputBaseFilename=APS-MIDI-Prep-Tool-Setup-{#AppVersion}
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
PrivilegesRequired=admin
PrivilegesRequiredOverridesAllowed=dialog
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
SetupIconFile=..\aps_midi_prep_tool_app\aps.ico
CloseApplications=yes

[Files]
Source: "{#Payload}"; DestDir: "{app}"; DestName: "APS MIDI Prep Tool.exe"; Flags: ignoreversion

[Tasks]
Name: "desktopicon"; Description: "Create a desktop shortcut"; Flags: unchecked

[Icons]
Name: "{group}\APS MIDI Prep Tool"; Filename: "{app}\APS MIDI Prep Tool.exe"; WorkingDir: "{app}"
Name: "{autodesktop}\APS MIDI Prep Tool"; Filename: "{app}\APS MIDI Prep Tool.exe"; WorkingDir: "{app}"; Tasks: desktopicon
