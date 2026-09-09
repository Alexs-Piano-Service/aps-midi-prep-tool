# PianoDisc and QRS controller evidence

Reviewed September 8, 2026. These sources support the seven profiles in
`preparation_profiles.py`. They establish file and delivery compatibility;
no profile claims hardware testing. PDF page numbers below are one-based.

| Profile | MIDI types established | App default |
| --- | --- | --- |
| PDS-128 Plus | Type 0 | 720 KB floppy |
| PDS-228CFX / SilentDrive | Type 0 | 720 KB floppy |
| Prodigy / Prodigy II (iQ Player) | Unspecified | App folder export |
| Chili / AMC | Types 0 and 1 | 1.44 MB floppy |
| PNOmation II / PMII | Unspecified | USB folder export |
| PNO3 | Unspecified | USB folder export |
| PNO4 / PNO4 Touch | Unspecified | USB folder export |

**PDS-128 Plus.** The [manufacturer manual hosted by In Tune Piano Services](https://www.intunepianoservices.com/PianoDisc%20PDS128%20Plus%20Owner%27s%20Manual.pdf),
printed p31, lists 720K and 1.44M formatting. Appendix C requires Type 0 for
user-created SMFs and permits piano parts on any MIDI channel. Indexed manual
text was accessible; the full scan exceeded the browser's size limit, so
Appendix C's PDF position was not visually verified. Unlike the 228CFX guide,
an explicit PC-formatted media statement was not recovered: ordinary FAT/SMF
delivery combines the documented SMF and density support. The app's 720 KB
default is conservative, not the only supported density. Do not extrapolate to
the original PDS-128.

**PDS-228CFX.** The [official user guide](https://pianodisc.com/wp-content/uploads/UserGuides/Silent%20Drive%20%28PDS-228%20CFX%29%20-%20User%20Guide.pdf),
printed p14/PDF25, explicitly accepts PC-formatted DD/HD SMF0 disks.
Printed p31/PDF42 offers 720K/1.44M formatting. Appendix C, printed p68/PDF79,
requires Type 0 and permits piano programs on any channel. Preparation combines
tracks into Type 0 while preserving channels, instruments, and timing; it does
not rewrite every part onto channel 1.

**Prodigy family.** The [Prodigy guide](https://pianodisc.com/wp-content/uploads/UserGuides/Prodigy%20User%20Guide.pdf),
pp6–10, describes MIDI connections; pp20–21 describe importing MIDI into iQ Player.
The [Prodigy II guide](https://pianodisc.com/wp-content/uploads/download-manager-files/Prodigy-2-manual-v1.pdf),
p29, documents Bluetooth, USB-C, and adapter MIDI. The [current iQ app guide](https://pianodisc.com/support/iq-app-guide/),
“Now Playing,” describes MIDI-file playback. The app prepares a folder for
import; these are not direct USB-stick playback claims. SMF types remain
unspecified. The [original iQ manual](https://pianodisc.com/wp-content/uploads/UserGuides/iQ%20-%20User%20Guide.pdf),
pp25–27,31, describes an audio input controller, which this profile does not cover.

**QRS USB controllers.** The [PMII guide](https://www.qrsmusic.com/assets/pdf/QRS%20PNOmation%20II%20User%20Guide%20and%20Upgrade%20Instructions_V553%20Rev%2020150909.pdf),
p19, explicitly supports USB-drive MIDI playback. The [2022 PNO3 guide](https://www.qrsmusic.com/assets/pdf/2022%20PNO3%20Owners%20Manual.pdf),
p46, identifies USB-stick content; the [2016 guide](https://www.qrsmusic.com/assets/pdf/QRS%20PNO3%20Quick%20Guide.pdf),
p28, distinguishes ordinary `.MID` from secured `.QRS` files. The [PNO4 guide](https://www.qrsmusic.com/assets/pdf/PNO4%20Owners%20Manual.pdf),
p11, supports owner music through USB/upload; p17 distinguishes ordinary MIDI.
SMF types, USB filesystems, and capacity limits were not established. Preserve
controller playback settings; do not infer universal channel routing.

**Chili / AMC.** The [Rev7 owner manual](https://www.qrsmusic.com/assets/pdf/im77500.pdf),
PDF1, explicitly supports SMF0/1. Printed p54/PDF15 specifies DOS HD media;
printed p5/PDF5 photographs a 3.5-inch floppy. The 1.44 MB default is inferred
from those facts, not a quoted numeric specification. Printed p51/PDF18 also
supports user MIDI data CDs; the app does not burn CDs. Emulator fit remains
manual configuration.

**Excluded routes.** [Petine](https://www.qrsmusic.com/assets/pdf/om990021.pdf)
and [Ancho](https://www.qrsmusic.com/assets/pdf/om980021.pdf), pp7,13,
support MIDI on CompactFlash/CD-R; there is no dedicated CF profile or writer.
[QRS2000C/2000CD+](https://www.qrsmusic.com/assets/pdf/om70390.pdf), pp9,13,
documents external MIDI and AMI audio, not a confirmed USB/floppy SMF route.
Ordinary MIDI export does not author secured QRS/SyncAlong packages, AMI or
PianoDisc encoded audio, or native PianoDisc System 3 disks. Controller support
does not establish a particular emulator's physical or electrical fit.
