"""Filename validation and concise guidance for recoverable dialog errors."""

_LANGUAGES = ("en", "es", "fr", "de", "it", "pt-BR", "bg", "nl", "pl", "ja", "ko", "zh-Hans")
VALIDATION_TRANSLATIONS = {}


def _add(sources, copies):
    if isinstance(sources, str):
        sources = (sources,)
    assert len(copies) == len(_LANGUAGES)
    for source in sources:
        VALIDATION_TRANSLATIONS[source] = dict(zip(_LANGUAGES, copies))


_add("Use printable Latin-1 characters only. Unsupported characters: {characters}", (
    "Use printable Latin-1 characters only. Unsupported characters: {characters}",
    "Usa solo caracteres imprimibles de Latin-1. Caracteres no compatibles: {characters}",
    "Utilisez uniquement des caractères imprimables Latin-1. Caractères non pris en charge : {characters}",
    "Verwenden Sie nur druckbare Latin-1-Zeichen. Nicht unterstützte Zeichen: {characters}",
    "Usa solo caratteri stampabili Latin-1. Caratteri non supportati: {characters}",
    "Use apenas caracteres imprimíveis Latin-1. Caracteres não compatíveis: {characters}",
    "Използвайте само печатаеми знаци от Latin-1. Неподдържани знаци: {characters}",
    "Gebruik alleen afdrukbare Latin-1-tekens. Niet-ondersteunde tekens: {characters}",
    "Używaj tylko drukowalnych znaków Latin-1. Nieobsługiwane znaki: {characters}",
    "表示可能な Latin-1 文字のみを使用してください。使用できない文字: {characters}",
    "출력 가능한 Latin-1 문자만 사용하세요. 지원하지 않는 문자: {characters}",
    "请仅使用可打印的 Latin-1 字符。不支持的字符：{characters}",
))
_add("Title display encoding", (
    "Title display encoding", "Codificación para mostrar títulos", "Encodage d’affichage des titres",
    "Zeichenkodierung für die Titelanzeige", "Codifica di visualizzazione dei titoli",
    "Codificação de exibição dos títulos", "Кодировка за показване на заглавията",
    "Tekencodering voor titelweergave", "Kodowanie wyświetlanych tytułów",
    "曲名表示の文字コード", "제목 표시 인코딩", "标题显示编码",
))
_add("Latin-1 (default)", (
    "Latin-1 (default)", "Latin-1 (predeterminado)", "Latin-1 (par défaut)",
    "Latin-1 (Standard)", "Latin-1 (predefinita)", "Latin-1 (padrão)",
    "Latin-1 (по подразбиране)", "Latin-1 (standaard)", "Latin-1 (domyślne)",
    "Latin-1（既定）", "Latin-1 (기본값)", "Latin-1（默认）",
))
_add("Windows-1252", ("Windows-1252",) * len(_LANGUAGES))
_add("Japanese (Shift-JIS)", (
    "Japanese (Shift-JIS)", "Japonés (Shift-JIS)", "Japonais (Shift-JIS)",
    "Japanisch (Shift-JIS)", "Giapponese (Shift-JIS)", "Japonês (Shift-JIS)",
    "Японски (Shift-JIS)", "Japans (Shift-JIS)", "Japońskie (Shift-JIS)",
    "日本語（Shift-JIS）", "일본어 (Shift-JIS)", "日文（Shift-JIS）",
))
_add("Simplified Chinese (GBK)", (
    "Simplified Chinese (GBK)", "Chino simplificado (GBK)", "Chinois simplifié (GBK)",
    "Chinesisch, vereinfacht (GBK)", "Cinese semplificato (GBK)", "Chinês simplificado (GBK)",
    "Опростен китайски (GBK)", "Vereenvoudigd Chinees (GBK)", "Chińskie uproszczone (GBK)",
    "簡体字中国語（GBK）", "중국어 간체 (GBK)", "简体中文（GBK）",
))
_add("Traditional Chinese (Big5)", (
    "Traditional Chinese (Big5)", "Chino tradicional (Big5)", "Chinois traditionnel (Big5)",
    "Chinesisch, traditionell (Big5)", "Cinese tradizionale (Big5)", "Chinês tradicional (Big5)",
    "Традиционен китайски (Big5)", "Traditioneel Chinees (Big5)", "Chińskie tradycyjne (Big5)",
    "繁体字中国語（Big5）", "중국어 번체 (Big5)", "繁体中文（Big5）",
))
_add("Choose how existing MIDI and E-SEQ title bytes are displayed. Files are unchanged; edited titles use Latin-1 and the destination's character restrictions.", (
    "Choose how existing MIDI and E-SEQ title bytes are displayed. Files are unchanged; edited titles use Latin-1 and the destination's character restrictions.",
    "Elige cómo se muestran los bytes de los títulos MIDI y E-SEQ existentes. Los archivos no se modifican; los títulos editados usan Latin-1 y las restricciones de caracteres del destino.",
    "Choisissez comment afficher les octets des titres MIDI et E-SEQ existants. Les fichiers restent inchangés ; les titres modifiés utilisent Latin-1 et respectent les restrictions de caractères de la destination.",
    "Wählen Sie, wie die vorhandenen Titelbytes in MIDI und E-SEQ angezeigt werden. Die Dateien bleiben unverändert; bearbeitete Titel verwenden Latin-1 und die Zeichenbeschränkungen des Ziels.",
    "Scegli come visualizzare i byte dei titoli MIDI ed E-SEQ esistenti. I file restano invariati; i titoli modificati usano Latin-1 e rispettano le restrizioni sui caratteri della destinazione.",
    "Escolha como os bytes dos títulos MIDI e E-SEQ existentes são exibidos. Os arquivos permanecem inalterados; os títulos editados usam Latin-1 e as restrições de caracteres do destino.",
    "Изберете как да се показват байтовете на съществуващите MIDI и E-SEQ заглавия. Файловете не се променят; редактираните заглавия използват Latin-1 и ограниченията за знаци на целевата система.",
    "Kies hoe de bytes van bestaande MIDI- en E-SEQ-titels worden weergegeven. De bestanden blijven ongewijzigd; bewerkte titels gebruiken Latin-1 en de tekenbeperkingen van de bestemming.",
    "Wybierz sposób wyświetlania bajtów istniejących tytułów MIDI i E-SEQ. Pliki pozostają niezmienione; edytowane tytuły używają Latin-1 i ograniczeń znaków systemu docelowego.",
    "既存の MIDI と E-SEQ の曲名バイト列の表示方法を選択します。ファイルは変更されません。編集した曲名には Latin-1 と出力先の文字制限が適用されます。",
    "기존 MIDI 및 E-SEQ 제목 바이트의 표시 방법을 선택하세요. 파일은 변경되지 않습니다. 편집한 제목은 Latin-1과 대상 시스템의 문자 제한을 따릅니다.",
    "选择现有 MIDI 和 E-SEQ 标题字节的显示方式。文件保持不变；编辑后的标题使用 Latin-1，并遵循目标系统的字符限制。",
))


_add("Filename cannot be empty.", (
    "Enter a filename.", "Introduce un nombre de archivo.", "Saisissez un nom de fichier.",
    "Geben Sie einen Dateinamen ein.", "Inserisci un nome file.", "Digite um nome de arquivo.",
    "Въведете име на файл.", "Voer een bestandsnaam in.", "Wpisz nazwę pliku.",
    "ファイル名を入力してください。", "파일 이름을 입력하세요.", "请输入文件名。",
))
_add("Filename cannot be '.' or '..'.", (
    "Choose a name other than '.' or '..'.", "Elige un nombre distinto de '.' o '..'.",
    "Choisissez un nom autre que « . » ou « .. ».", "Wählen Sie einen anderen Namen als '.' oder '..'.",
    "Scegli un nome diverso da '.' o '..'.", "Escolha um nome diferente de '.' ou '..'.",
    "Изберете име, различно от '.' или '..'.", "Kies een andere naam dan '.' of '..'.",
    "Wybierz nazwę inną niż '.' lub '..'.", "「.」や「..」以外の名前を指定してください。",
    "'.' 또는 '..' 이외의 이름을 선택하세요.", "请选择“.”或“..”以外的名称。",
))
_add("Filename cannot end with '.'.", (
    "Remove the period at the end of the filename.", "Quita el punto al final del nombre de archivo.",
    "Retirez le point à la fin du nom de fichier.", "Entfernen Sie den Punkt am Ende des Dateinamens.",
    "Rimuovi il punto alla fine del nome file.", "Remova o ponto no final do nome do arquivo.",
    "Премахнете точката в края на името на файла.", "Verwijder de punt aan het einde van de bestandsnaam.",
    "Usuń kropkę na końcu nazwy pliku.", "ファイル名の末尾のピリオドを削除してください。",
    "파일 이름 끝의 마침표를 삭제하세요.", "请删除文件名末尾的句点。",
))
_add("Filename cannot end with a space.", (
    "Remove the space at the end of the filename.", "Quita el espacio al final del nombre de archivo.",
    "Retirez l'espace à la fin du nom de fichier.", "Entfernen Sie das Leerzeichen am Ende des Dateinamens.",
    "Rimuovi lo spazio alla fine del nome file.", "Remova o espaço no final do nome do arquivo.",
    "Премахнете интервала в края на името на файла.", "Verwijder de spatie aan het einde van de bestandsnaam.",
    "Usuń spację na końcu nazwy pliku.", "ファイル名の末尾の空白を削除してください。",
    "파일 이름 끝의 공백을 삭제하세요.", "请删除文件名末尾的空格。",
))
_add("Filename contains characters that are not valid in FAT filenames.", (
    'Remove these characters from the filename: \\ / : * ? " < > |',
    'Quita estos caracteres del nombre de archivo: \\ / : * ? " < > |',
    'Retirez ces caractères du nom de fichier : \\ / : * ? " < > |',
    'Entfernen Sie diese Zeichen aus dem Dateinamen: \\ / : * ? " < > |',
    'Rimuovi questi caratteri dal nome file: \\ / : * ? " < > |',
    'Remova estes caracteres do nome do arquivo: \\ / : * ? " < > |',
    'Премахнете тези знаци от името на файла: \\ / : * ? " < > |',
    'Verwijder deze tekens uit de bestandsnaam: \\ / : * ? " < > |',
    'Usuń te znaki z nazwy pliku: \\ / : * ? " < > |',
    'ファイル名から次の文字を削除してください: \\ / : * ? " < > |',
    '파일 이름에서 다음 문자를 삭제하세요: \\ / : * ? " < > |',
    '请从文件名中删除这些字符：\\ / : * ? " < > |',
))
_add("Filename cannot contain control characters.", (
    "Remove tabs, line breaks, and other control characters from the filename.",
    "Quita tabulaciones, saltos de línea y otros caracteres de control del nombre de archivo.",
    "Retirez les tabulations, sauts de ligne et autres caractères de contrôle du nom de fichier.",
    "Entfernen Sie Tabulatoren, Zeilenumbrüche und andere Steuerzeichen aus dem Dateinamen.",
    "Rimuovi tabulazioni, interruzioni di riga e altri caratteri di controllo dal nome file.",
    "Remova tabulações, quebras de linha e outros caracteres de controle do nome do arquivo.",
    "Премахнете табулациите, новите редове и другите контролни знаци от името на файла.",
    "Verwijder tabs, regeleinden en andere besturingstekens uit de bestandsnaam.",
    "Usuń tabulatory, znaki nowego wiersza i inne znaki sterujące z nazwy pliku.",
    "ファイル名からタブ、改行などの制御文字を削除してください。",
    "파일 이름에서 탭, 줄 바꿈 및 기타 제어 문자를 삭제하세요.",
    "请从文件名中删除制表符、换行符及其他控制字符。",
))
_add("Filename must be 255 characters or fewer.", (
    "Shorten the filename to 255 characters or fewer.", "Acorta el nombre de archivo a 255 caracteres o menos.",
    "Raccourcissez le nom de fichier à 255 caractères maximum.", "Kürzen Sie den Dateinamen auf höchstens 255 Zeichen.",
    "Riduci il nome file a un massimo di 255 caratteri.", "Reduza o nome do arquivo para no máximo 255 caracteres.",
    "Съкратете името на файла до най-много 255 знака.", "Kort de bestandsnaam in tot maximaal 255 tekens.",
    "Skróć nazwę pliku do maksymalnie 255 znaków.", "ファイル名を 255 文字以内に短くしてください。",
    "파일 이름을 255자 이하로 줄이세요.", "请将文件名缩短至 255 个字符以内。",
))
_add("Filename must have a name before the extension.", (
    "Enter a name before the extension, such as SONG.MID.", "Escribe un nombre antes de la extensión, como SONG.MID.",
    "Saisissez un nom avant l'extension, par exemple SONG.MID.", "Geben Sie vor der Erweiterung einen Namen ein, etwa SONG.MID.",
    "Inserisci un nome prima dell'estensione, ad esempio SONG.MID.", "Digite um nome antes da extensão, como SONG.MID.",
    "Въведете име преди разширението, например SONG.MID.", "Voer een naam vóór de extensie in, zoals SONG.MID.",
    "Wpisz nazwę przed rozszerzeniem, np. SONG.MID.", "SONG.MID のように、拡張子の前に名前を付けてください。",
    "SONG.MID처럼 확장자 앞에 이름을 입력하세요.", "请在扩展名前输入名称，例如 SONG.MID。",
))
_add("{filename} is managed automatically.", (
    "{filename} is managed by the app. Choose another name.",
    "La aplicación gestiona {filename}. Elige otro nombre.",
    "L'application gère {filename}. Choisissez un autre nom.",
    "{filename} wird von der App verwaltet. Wählen Sie einen anderen Namen.",
    "{filename} è gestito dall'app. Scegli un altro nome.",
    "O aplicativo gerencia {filename}. Escolha outro nome.",
    "{filename} се управлява от приложението. Изберете друго име.",
    "De app beheert {filename}. Kies een andere naam.",
    "Aplikacja zarządza plikiem {filename}. Wybierz inną nazwę.",
    "{filename} はアプリが管理しています。別の名前を指定してください。",
    "{filename} 파일은 앱에서 관리합니다. 다른 이름을 선택하세요.",
    "{filename} 由应用管理。请选择其他名称。",
))
_add("Use printable ASCII only (space through ~). Unsupported characters: {characters}", (
    "Use printable ASCII only (space through ~). Unsupported characters: {characters}",
    "Usa solo ASCII imprimible (del espacio a ~). Caracteres no admitidos: {characters}",
    "Utilisez uniquement l'ASCII imprimable (de l'espace à ~). Caractères non pris en charge : {characters}",
    "Verwenden Sie nur druckbare ASCII-Zeichen (Leerzeichen bis ~). Nicht unterstützte Zeichen: {characters}",
    "Usa solo caratteri ASCII stampabili (dallo spazio a ~). Caratteri non supportati: {characters}",
    "Use apenas ASCII imprimível (do espaço até ~). Caracteres não aceitos: {characters}",
    "Използвайте само печатаеми ASCII знаци (от интервал до ~). Неподдържани знаци: {characters}",
    "Gebruik alleen afdrukbare ASCII-tekens (van spatie tot ~). Niet-ondersteunde tekens: {characters}",
    "Używaj tylko drukowalnych znaków ASCII (od spacji do ~). Nieobsługiwane znaki: {characters}",
    "印字可能な ASCII 文字（半角スペースから ~ まで）のみ使用できます。使用できない文字: {characters}",
    "출력 가능한 ASCII 문자(공백부터 ~까지)만 사용하세요. 지원하지 않는 문자: {characters}",
    "请仅使用可打印的 ASCII 字符（空格到 ~）。不支持的字符：{characters}",
))

_add((
    "The source files were not modified", "The source BLK files were not modified",
    "The source disk or image was not modified", "The image and MIDI file have not been changed",
), (
    "The originals are unchanged.", "Los originales no han cambiado.", "Les originaux sont inchangés.",
    "Die Originale sind unverändert.", "Gli originali sono invariati.", "Os originais não foram alterados.",
    "Оригиналите са непроменени.", "De originelen zijn ongewijzigd.", "Oryginały pozostały bez zmian.",
    "元のデータは変更されていません。", "원본은 변경되지 않았습니다.", "原始数据未更改。",
))
_add((
    "The source image was not modified. Review the converted MIDI files before using them for preservation or playback",
    "The source files were not modified; review the MIDI files that were created",
), (
    "The originals are unchanged. Check the exported MIDI files before using them.",
    "Los originales no han cambiado. Revisa los archivos MIDI exportados antes de usarlos.",
    "Les originaux sont inchangés. Vérifiez les fichiers MIDI exportés avant de les utiliser.",
    "Die Originale sind unverändert. Prüfen Sie die exportierten MIDI-Dateien vor der Verwendung.",
    "Gli originali sono invariati. Controlla i file MIDI esportati prima di usarli.",
    "Os originais não foram alterados. Confira os arquivos MIDI exportados antes de usá-los.",
    "Оригиналите са непроменени. Проверете експортираните MIDI файлове преди употреба.",
    "De originelen zijn ongewijzigd. Controleer de geëxporteerde MIDI-bestanden vóór gebruik.",
    "Oryginały pozostały bez zmian. Sprawdź wyeksportowane pliki MIDI przed użyciem.",
    "元のデータは変更されていません。書き出した MIDI ファイルを使用前に確認してください。",
    "원본은 변경되지 않았습니다. 내보낸 MIDI 파일을 사용하기 전에 확인하세요.",
    "原始数据未更改。请在使用前检查导出的 MIDI 文件。",
))
_add("Review the converted MIDI files before using them for preservation or playback", (
    "Check the converted MIDI files before using them.", "Revisa los archivos MIDI convertidos antes de usarlos.",
    "Vérifiez les fichiers MIDI convertis avant de les utiliser.", "Prüfen Sie die konvertierten MIDI-Dateien vor der Verwendung.",
    "Controlla i file MIDI convertiti prima di usarli.", "Confira os arquivos MIDI convertidos antes de usá-los.",
    "Проверете преобразуваните MIDI файлове преди употреба.", "Controleer de geconverteerde MIDI-bestanden vóór gebruik.",
    "Sprawdź przekonwertowane pliki MIDI przed użyciem.", "変換した MIDI ファイルを使用前に確認してください。",
    "변환한 MIDI 파일을 사용하기 전에 확인하세요.", "请在使用前检查转换后的 MIDI 文件。",
))
_add("Review the converted MIDI files and retain the original BLK files as the preservation copies", (
    "Check the converted MIDI files. Keep the original BLK files as backups.",
    "Revisa los archivos MIDI convertidos. Conserva los BLK originales como copia de seguridad.",
    "Vérifiez les fichiers MIDI convertis. Conservez les originaux BLK comme sauvegardes.",
    "Prüfen Sie die konvertierten MIDI-Dateien. Bewahren Sie die BLK-Originale als Sicherung auf.",
    "Controlla i file MIDI convertiti. Conserva gli originali BLK come copie di sicurezza.",
    "Confira os arquivos MIDI convertidos. Guarde os originais BLK como cópias de segurança.",
    "Проверете преобразуваните MIDI файлове. Запазете оригиналните BLK файлове като резервни копия.",
    "Controleer de geconverteerde MIDI-bestanden. Bewaar de originele BLK-bestanden als back-up.",
    "Sprawdź przekonwertowane pliki MIDI. Zachowaj oryginalne pliki BLK jako kopie zapasowe.",
    "変換した MIDI ファイルを確認してください。元の BLK ファイルをバックアップとして保管してください。",
    "변환한 MIDI 파일을 확인하세요. 원본 BLK 파일을 백업으로 보관하세요.",
    "请检查转换后的 MIDI 文件，并保留原始 BLK 文件作为备份。",
))
_add((
    "Unsupported or unreadable files were skipped; the files already added remain staged",
    "Unreadable files were skipped; the files already added remain staged",
), (
    "Some files were skipped. Added files are still waiting to be saved.",
    "Se omitieron algunos archivos. Los archivos añadidos siguen pendientes de guardar.",
    "Certains fichiers ont été ignorés. Les fichiers ajoutés restent en attente d'enregistrement.",
    "Einige Dateien wurden übersprungen. Hinzugefügte Dateien warten weiterhin auf das Speichern.",
    "Alcuni file sono stati ignorati. I file aggiunti sono ancora in attesa di salvataggio.",
    "Alguns arquivos foram ignorados. Os arquivos adicionados ainda aguardam o salvamento.",
    "Някои файлове бяха пропуснати. Добавените файлове все още чакат записване.",
    "Sommige bestanden zijn overgeslagen. Toegevoegde bestanden wachten nog op opslag.",
    "Niektóre pliki pominięto. Dodane pliki nadal czekają na zapis.",
    "一部のファイルはスキップされました。追加済みのファイルは保存待ちです。",
    "일부 파일을 건너뛰었습니다. 추가된 파일은 아직 저장 대기 중입니다.",
    "已跳过部分文件。已添加的文件仍在等待保存。",
))
_add("The original files were not changed; remove or replace the listed files and try again", (
    "The originals are unchanged. Remove or replace the listed files and try again.",
    "Los originales no han cambiado. Quita o sustituye los archivos indicados y vuelve a intentarlo.",
    "Les originaux sont inchangés. Retirez ou remplacez les fichiers indiqués, puis réessayez.",
    "Die Originale sind unverändert. Entfernen oder ersetzen Sie die aufgeführten Dateien und versuchen Sie es erneut.",
    "Gli originali sono invariati. Rimuovi o sostituisci i file indicati e riprova.",
    "Os originais não foram alterados. Remova ou substitua os arquivos indicados e tente novamente.",
    "Оригиналите са непроменени. Премахнете или заменете посочените файлове и опитайте отново.",
    "De originelen zijn ongewijzigd. Verwijder of vervang de vermelde bestanden en probeer opnieuw.",
    "Oryginały pozostały bez zmian. Usuń lub zastąp wskazane pliki i spróbuj ponownie.",
    "元のファイルは変更されていません。一覧のファイルを除外または置き換えて、再試行してください。",
    "원본은 변경되지 않았습니다. 표시된 파일을 제거하거나 교체한 후 다시 시도하세요.",
    "原始文件未更改。请移除或替换列出的文件后重试。",
))
_add("Nothing has been written yet; remove or replace the listed files and try again", (
    "Nothing has been saved. Remove or replace the listed files and try again.",
    "No se ha guardado nada. Quita o sustituye los archivos indicados y vuelve a intentarlo.",
    "Rien n'a été enregistré. Retirez ou remplacez les fichiers indiqués, puis réessayez.",
    "Es wurde noch nichts gespeichert. Entfernen oder ersetzen Sie die aufgeführten Dateien und versuchen Sie es erneut.",
    "Non è stato salvato nulla. Rimuovi o sostituisci i file indicati e riprova.",
    "Nada foi salvo. Remova ou substitua os arquivos indicados e tente novamente.",
    "Нищо не е записано. Премахнете или заменете посочените файлове и опитайте отново.",
    "Er is niets opgeslagen. Verwijder of vervang de vermelde bestanden en probeer opnieuw.",
    "Nic nie zapisano. Usuń lub zastąp wskazane pliki i spróbuj ponownie.",
    "まだ何も保存されていません。一覧のファイルを除外または置き換えて、再試行してください。",
    "아직 저장된 내용이 없습니다. 표시된 파일을 제거하거나 교체한 후 다시 시도하세요.",
    "尚未保存任何内容。请移除或替换列出的文件后重试。",
))
_add("The affected files were converted with their original titles", (
    "These files were converted with their original titles.", "Estos archivos se convirtieron con sus títulos originales.",
    "Ces fichiers ont été convertis avec leurs titres d'origine.", "Diese Dateien wurden mit ihren ursprünglichen Titeln konvertiert.",
    "Questi file sono stati convertiti con i titoli originali.", "Estes arquivos foram convertidos com os títulos originais.",
    "Тези файлове са преобразувани с оригиналните си заглавия.", "Deze bestanden zijn met hun oorspronkelijke titels geconverteerd.",
    "Te pliki przekonwertowano z oryginalnymi tytułami.", "これらのファイルは元の曲名のまま変換されました。",
    "이 파일들은 원래 제목으로 변환되었습니다.", "这些文件已使用原始曲名完成转换。",
))
_add("The other additions remain staged; remove or replace the listed files before saving", (
    "Other additions are still waiting to be saved. Remove or replace the listed files before saving.",
    "Las demás adiciones siguen pendientes de guardar. Quita o sustituye los archivos indicados antes de guardar.",
    "Les autres ajouts restent en attente d'enregistrement. Retirez ou remplacez les fichiers indiqués avant d'enregistrer.",
    "Andere hinzugefügte Dateien warten weiterhin auf das Speichern. Entfernen oder ersetzen Sie die aufgeführten Dateien vor dem Speichern.",
    "Le altre aggiunte sono ancora in attesa di salvataggio. Rimuovi o sostituisci i file indicati prima di salvare.",
    "As outras adições ainda aguardam o salvamento. Remova ou substitua os arquivos indicados antes de salvar.",
    "Останалите добавени файлове все още чакат записване. Премахнете или заменете посочените файлове преди записване.",
    "Andere toevoegingen wachten nog op opslag. Verwijder of vervang de vermelde bestanden voordat je opslaat.",
    "Pozostałe dodane pliki nadal czekają na zapis. Usuń lub zastąp wskazane pliki przed zapisaniem.",
    "他の追加済みファイルは保存待ちです。保存する前に、一覧のファイルを除外または置き換えてください。",
    "다른 추가 파일은 아직 저장 대기 중입니다. 저장하기 전에 표시된 파일을 제거하거나 교체하세요.",
    "其他已添加的文件仍在等待保存。请在保存前移除或替换列出的文件。",
))
_add("Fix the listed files, then try Save again", (
    "Fix the listed files, then save again.", "Corrige los archivos indicados y vuelve a guardar.",
    "Corrigez les fichiers indiqués, puis enregistrez à nouveau.", "Korrigieren Sie die aufgeführten Dateien und speichern Sie erneut.",
    "Correggi i file indicati e salva di nuovo.", "Corrija os arquivos indicados e salve novamente.",
    "Поправете посочените файлове и запишете отново.", "Herstel de vermelde bestanden en sla opnieuw op.",
    "Popraw wskazane pliki i zapisz ponownie.", "一覧のファイルを修正して、再度保存してください。",
    "표시된 파일을 수정한 후 다시 저장하세요.", "请修正列出的文件后重新保存。",
))
_add("The original files were not modified; fix the listed files and try Save As again", (
    "The originals are unchanged. Fix the listed files, then export again.",
    "Los originales no han cambiado. Corrige los archivos indicados y vuelve a exportar.",
    "Les originaux sont inchangés. Corrigez les fichiers indiqués, puis exportez à nouveau.",
    "Die Originale sind unverändert. Korrigieren Sie die aufgeführten Dateien und exportieren Sie erneut.",
    "Gli originali sono invariati. Correggi i file indicati ed esporta di nuovo.",
    "Os originais não foram alterados. Corrija os arquivos indicados e exporte novamente.",
    "Оригиналите са непроменени. Поправете посочените файлове и експортирайте отново.",
    "De originelen zijn ongewijzigd. Herstel de vermelde bestanden en exporteer opnieuw.",
    "Oryginały pozostały bez zmian. Popraw wskazane pliki i wyeksportuj ponownie.",
    "元のファイルは変更されていません。一覧のファイルを修正して、再度書き出してください。",
    "원본은 변경되지 않았습니다. 표시된 파일을 수정한 후 다시 내보내세요.",
    "原始文件未更改。请修正列出的文件后重新导出。",
))
_add((
    "Check that the destination folder is writable and that enough disk space is available",
    "Check that the destination folder is writable, then try again",
), (
    "Check folder write permissions and free disk space, then try again.",
    "Comprueba los permisos de escritura de la carpeta y el espacio libre, y vuelve a intentarlo.",
    "Vérifiez les droits d'écriture du dossier et l'espace libre, puis réessayez.",
    "Prüfen Sie die Schreibrechte für den Ordner und den freien Speicherplatz und versuchen Sie es erneut.",
    "Controlla i permessi di scrittura della cartella e lo spazio libero, poi riprova.",
    "Confira a permissão de gravação na pasta e o espaço livre, depois tente novamente.",
    "Проверете правата за запис в папката и свободното място, после опитайте отново.",
    "Controleer de schrijfrechten voor de map en de vrije schijfruimte en probeer opnieuw.",
    "Sprawdź uprawnienia zapisu do folderu i wolne miejsce na dysku, a potem spróbuj ponownie.",
    "フォルダーの書き込み権限とディスクの空き容量を確認し、再試行してください。",
    "폴더의 쓰기 권한과 디스크 여유 공간을 확인한 후 다시 시도하세요.",
    "请检查文件夹写入权限和磁盘可用空间后重试。",
))
_add("The app continued with the files it could read", (
    "Continued with the files that could be read.", "Se continuó con los archivos que pudieron leerse.",
    "Le traitement a continué avec les fichiers lisibles.", "Die Verarbeitung wurde mit den lesbaren Dateien fortgesetzt.",
    "L'operazione è proseguita con i file leggibili.", "O processo continuou com os arquivos que puderam ser lidos.",
    "Операцията продължи с файловете, които могат да се прочетат.", "Verdergegaan met de leesbare bestanden.",
    "Kontynuowano z plikami, które udało się odczytać.", "読み取れたファイルで処理を続行しました。",
    "읽을 수 있는 파일로 계속 진행했습니다.", "已使用能够读取的文件继续处理。",
))
_add(("Move or rename the conflicting files, then try again", "Rename conflicting files and try again"), (
    "Rename the files with conflicting names and try again.", "Cambia los nombres de los archivos en conflicto y vuelve a intentarlo.",
    "Renommez les fichiers dont les noms sont en conflit, puis réessayez.", "Benennen Sie Dateien mit Namenskonflikten um und versuchen Sie es erneut.",
    "Rinomina i file con nomi in conflitto e riprova.", "Renomeie os arquivos com nomes em conflito e tente novamente.",
    "Преименувайте файловете с конфликтни имена и опитайте отново.", "Hernoem de bestanden met conflicterende namen en probeer opnieuw.",
    "Zmień nazwy plików powodujących konflikt i spróbuj ponownie.", "名前が重複するファイルを改名して、再試行してください。",
    "이름이 충돌하는 파일의 이름을 바꾼 후 다시 시도하세요.", "请重命名名称冲突的文件后重试。",
))
_add("Check that the source files still exist and that the generated names do not conflict", (
    "Check that source files exist and output names are unique.", "Comprueba que existan los archivos de origen y que los nombres de salida no se repitan.",
    "Vérifiez que les fichiers sources existent et que les noms de sortie sont uniques.", "Prüfen Sie, ob die Quelldateien vorhanden und die Ausgabenamen eindeutig sind.",
    "Verifica che i file sorgente esistano e che i nomi di destinazione siano univoci.", "Confira se os arquivos de origem existem e se os nomes de saída são únicos.",
    "Проверете дали изходните файлове съществуват и дали новите имена са уникални.", "Controleer of de bronbestanden bestaan en de uitvoernamen uniek zijn.",
    "Sprawdź, czy pliki źródłowe istnieją i czy nazwy wyjściowe są unikatowe.", "元ファイルが存在し、出力名が重複していないことを確認してください。",
    "원본 파일이 있는지, 출력 이름이 중복되지 않는지 확인하세요.", "请确认源文件仍然存在，并且输出名称没有重复。",
))
_add("The current floppy/image session is still open; choose a writable destination folder and try again", (
    "Keep this session open. Choose a writable output folder and try again.", "Mantén abierta esta sesión. Elige una carpeta de salida con permiso de escritura y vuelve a intentarlo.",
    "Gardez cette session ouverte. Choisissez un dossier de sortie accessible en écriture et réessayez.", "Lassen Sie diese Sitzung geöffnet. Wählen Sie einen beschreibbaren Ausgabeordner und versuchen Sie es erneut.",
    "Lascia aperta questa sessione. Scegli una cartella di destinazione scrivibile e riprova.", "Mantenha esta sessão aberta. Escolha uma pasta de saída com permissão de gravação e tente novamente.",
    "Оставете сесията отворена. Изберете изходна папка с права за запис и опитайте отново.", "Houd deze sessie open. Kies een beschrijfbare uitvoermap en probeer opnieuw.",
    "Pozostaw tę sesję otwartą. Wybierz folder wyjściowy z prawem zapisu i spróbuj ponownie.", "このセッションを閉じずに、書き込み可能な出力先フォルダーを選んで再試行してください。",
    "이 세션을 열어 두세요. 쓰기 가능한 출력 폴더를 선택한 후 다시 시도하세요.", "请保持此会话打开，选择可写入的输出文件夹后重试。",
))
_add("Check that the image folder is writable, or turn off backups before saving", (
    "Allow writing to the image folder, or turn off backups before saving.", "Permite la escritura en la carpeta de la imagen o desactiva las copias de seguridad antes de guardar.",
    "Autorisez l'écriture dans le dossier de l'image ou désactivez les sauvegardes avant d'enregistrer.", "Erlauben Sie das Schreiben in den Imageordner oder deaktivieren Sie Sicherungen vor dem Speichern.",
    "Consenti la scrittura nella cartella dell'immagine o disattiva le copie di sicurezza prima di salvare.", "Permita a gravação na pasta da imagem ou desative as cópias de segurança antes de salvar.",
    "Разрешете записа в папката на образа или изключете резервните копия преди записване.", "Sta schrijven naar de imagemap toe of schakel back-ups uit voordat je opslaat.",
    "Zezwól na zapis do folderu obrazu lub wyłącz kopie zapasowe przed zapisaniem.", "イメージのフォルダーへの書き込みを許可するか、バックアップを無効にしてから保存してください。",
    "이미지 폴더에 쓰기를 허용하거나 백업을 끈 후 저장하세요.", "请允许写入映像文件夹，或关闭备份后再保存。",
))
_add("Keep this session open and try Save As Image if you need a recoverable copy", (
    "Keep this session open and export a disk image to save a copy.", "Mantén abierta esta sesión y exporta una imagen de disco para guardar una copia.",
    "Gardez cette session ouverte et exportez une image disque pour en conserver une copie.", "Lassen Sie diese Sitzung geöffnet und exportieren Sie ein Diskettenimage, um eine Kopie zu speichern.",
    "Lascia aperta questa sessione ed esporta un'immagine disco per salvare una copia.", "Mantenha esta sessão aberta e exporte uma imagem de disco para salvar uma cópia.",
    "Оставете сесията отворена и експортирайте дисков образ, за да запазите копие.", "Houd deze sessie open en exporteer een diskimage om een kopie te bewaren.",
    "Pozostaw tę sesję otwartą i wyeksportuj obraz dysku, aby zachować kopię.", "このセッションを閉じずに、ディスクイメージを書き出してコピーを保存してください。",
    "이 세션을 열어 두고 디스크 이미지를 내보내 사본을 저장하세요.", "请保持此会话打开，并导出磁盘映像以保存副本。",
))
_add("The pending changes are still listed; check the file location and try again", (
    "Changes are still listed. Check the file location and try again.", "Los cambios siguen en la lista. Comprueba la ubicación del archivo y vuelve a intentarlo.",
    "Les modifications sont toujours dans la liste. Vérifiez l'emplacement du fichier et réessayez.", "Die Änderungen stehen weiterhin in der Liste. Prüfen Sie den Speicherort und versuchen Sie es erneut.",
    "Le modifiche sono ancora nell'elenco. Controlla la posizione del file e riprova.", "As alterações continuam na lista. Confira o local do arquivo e tente novamente.",
    "Промените все още са в списъка. Проверете местоположението на файла и опитайте отново.", "De wijzigingen staan nog in de lijst. Controleer de bestandslocatie en probeer opnieuw.",
    "Zmiany nadal są na liście. Sprawdź położenie pliku i spróbuj ponownie.", "変更はリストに残っています。ファイルの場所を確認して、再試行してください。",
    "변경 사항은 목록에 남아 있습니다. 파일 위치를 확인한 후 다시 시도하세요.", "更改仍保留在列表中。请检查文件位置后重试。",
))
_add("No completed conversion rows were cleared; fix the listed files and try Save or Save As again", (
    "Converted files remain listed. Fix the errors, then save or export again.", "Los archivos convertidos siguen en la lista. Corrige los errores y vuelve a guardar o exportar.",
    "Les fichiers convertis restent dans la liste. Corrigez les erreurs, puis enregistrez ou exportez à nouveau.", "Die konvertierten Dateien stehen weiterhin in der Liste. Beheben Sie die Fehler und speichern oder exportieren Sie erneut.",
    "I file convertiti restano nell'elenco. Correggi gli errori, poi salva o esporta di nuovo.", "Os arquivos convertidos continuam na lista. Corrija os erros e salve ou exporte novamente.",
    "Преобразуваните файлове остават в списъка. Отстранете грешките, после запишете или експортирайте отново.", "De geconverteerde bestanden blijven in de lijst. Herstel de fouten en sla op of exporteer opnieuw.",
    "Przekonwertowane pliki pozostają na liście. Popraw błędy i zapisz lub wyeksportuj ponownie.", "変換済みのファイルはリストに残っています。エラーを修正して、再度保存または書き出してください。",
    "변환된 파일은 목록에 남아 있습니다. 오류를 수정한 후 다시 저장하거나 내보내세요.", "转换后的文件仍保留在列表中。请修正错误后重新保存或导出。",
))
