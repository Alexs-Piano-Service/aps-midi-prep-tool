"""Localized backup guidance; file paths and external diagnostics stay intact."""

_LANGUAGES = ("es", "fr", "de", "it", "pt-BR", "bg", "nl", "pl", "ja", "ko", "zh-Hans")


def _translations(*values):
    return dict(zip(_LANGUAGES, values, strict=True))


MARKIV_BACKUP_DIAGNOSTICS_TRANSLATIONS = {
    "Truncated MIDI quantity": _translations(
        "Cantidad MIDI truncada", "Valeur MIDI tronquée", "Abgeschnittener MIDI-Wert", "Valore MIDI troncato", "Valor MIDI truncado", "Отрязана MIDI стойност",
        "Afgebroken MIDI-waarde", "Ucięta wartość MIDI", "MIDI値が切り詰められています", "MIDI 값이 잘렸습니다", "MIDI 数值被截断",
    ),
    "Oversized MIDI quantity": _translations(
        "Cantidad MIDI demasiado grande", "Valeur MIDI trop grande", "Zu großer MIDI-Wert", "Valore MIDI troppo grande", "Valor MIDI muito grande", "Твърде голяма MIDI стойност",
        "Te grote MIDI-waarde", "Zbyt duża wartość MIDI", "MIDI値が大きすぎます", "MIDI 값이 너무 큽니다", "MIDI 数值过大",
    ),
    "Complete": _translations(
        "Completo", "Terminé", "Abgeschlossen", "Completo", "Concluído", "Завършено", "Voltooid", "Ukończono", "完了", "완료", "已完成",
    ),
    "Cancelled": _translations(
        "Cancelado", "Annulé", "Abgebrochen", "Annullato", "Cancelado", "Отменено", "Geannuleerd", "Anulowano", "キャンセル済み", "취소됨", "已取消",
    ),
    "Incomplete": _translations(
        "Incompleto", "Incomplet", "Unvollständig", "Incompleto", "Incompleto", "Незавършено", "Onvolledig", "Niekompletne", "未完了", "미완료", "未完成",
    ),
    "In progress": _translations(
        "En curso", "En cours", "In Bearbeitung", "In corso", "Em andamento", "В процес на изпълнение", "Bezig", "W toku", "進行中", "진행 중", "进行中",
    ),
    "Choose a backup folder outside the source drive or source folder.": _translations(
        "Elige una carpeta de copia fuera de la unidad o carpeta de origen.",
        "Choisissez un dossier de sauvegarde hors du lecteur ou du dossier source.",
        "Wählen Sie einen Sicherungsordner außerhalb des Quelllaufwerks oder Quellordners.",
        "Scegli una cartella di backup esterna all'unità o alla cartella di origine.",
        "Escolha uma pasta de backup fora da unidade ou pasta de origem.",
        "Изберете папка за архивиране извън изходното устройство или папка.",
        "Kies een back-upmap buiten het bronstation of de bronmap.",
        "Wybierz folder kopii zapasowej poza dyskiem lub folderem źródłowym.",
        "元のドライブまたはフォルダーの外にバックアップ先を選択してください。",
        "원본 드라이브 또는 폴더 외부의 백업 폴더를 선택하세요.",
        "请选择源驱动器或源文件夹以外的备份文件夹。",
    ),
    "The destination must be a folder.": _translations(
        "El destino debe ser una carpeta.", "La destination doit être un dossier.",
        "Das Ziel muss ein Ordner sein.", "La destinazione deve essere una cartella.",
        "O destino deve ser uma pasta.", "Местоназначението трябва да е папка.",
        "De bestemming moet een map zijn.", "Miejsce docelowe musi być folderem.",
        "保存先にはフォルダーを指定してください。", "대상은 폴더여야 합니다.", "目标必须是文件夹。",
    ),
    "The backup destination must be on a different filesystem from the source drive.": _translations(
        "El destino de la copia debe estar en un sistema de archivos distinto al de la unidad de origen.",
        "La destination de sauvegarde doit se trouver sur un autre système de fichiers que le lecteur source.",
        "Das Sicherungsziel muss auf einem anderen Dateisystem als das Quelllaufwerk liegen.",
        "La destinazione del backup deve trovarsi su un file system diverso da quello dell'unità di origine.",
        "O destino do backup deve estar em um sistema de arquivos diferente da unidade de origem.",
        "Архивът трябва да е на различна файлова система от изходното устройство.",
        "De back-upbestemming moet op een ander bestandssysteem staan dan het bronstation.",
        "Kopia zapasowa musi znajdować się w innym systemie plików niż dysk źródłowy.",
        "バックアップ先は元のドライブとは別のファイルシステム上にある必要があります。",
        "백업 대상은 원본 드라이브와 다른 파일 시스템에 있어야 합니다.",
        "备份目标必须与源驱动器位于不同的文件系统。",
    ),
    "Not enough free space for the backup and its manifest.": _translations(
        "No hay espacio suficiente para la copia y su manifiesto.", "Espace insuffisant pour la sauvegarde et son manifeste.",
        "Nicht genügend freier Speicherplatz für die Sicherung und ihr Manifest.", "Spazio insufficiente per il backup e il relativo manifesto.",
        "Espaço insuficiente para o backup e seu manifesto.", "Недостатъчно свободно място за архива и неговия манифест.",
        "Onvoldoende vrije ruimte voor de back-up en het manifest.", "Za mało wolnego miejsca na kopię zapasową i jej manifest.",
        "バックアップとマニフェストの空き容量が不足しています。", "백업 및 매니페스트를 위한 공간이 부족합니다.", "没有足够空间存放备份及其清单。",
    ),
    "Not enough free space for the backup, MIDI conversions, and manifest.": _translations(
        "No hay espacio suficiente para la copia, las conversiones MIDI y el manifiesto.", "Espace insuffisant pour la sauvegarde, les conversions MIDI et le manifeste.",
        "Nicht genügend freier Speicherplatz für Sicherung, MIDI-Konvertierungen und Manifest.", "Spazio insufficiente per il backup, le conversioni MIDI e il manifesto.",
        "Espaço insuficiente para o backup, as conversões MIDI e o manifesto.", "Недостатъчно свободно място за архива, MIDI преобразуванията и манифеста.",
        "Onvoldoende vrije ruimte voor de back-up, MIDI-conversies en het manifest.", "Za mało wolnego miejsca na kopię zapasową, konwersje MIDI i manifest.",
        "バックアップ、MIDI変換、マニフェストの空き容量が不足しています。", "백업, MIDI 변환 및 매니페스트를 위한 공간이 부족합니다.", "没有足够空间存放备份、MIDI 转换文件及清单。",
    ),
    "Not enough free space for the MIDI conversion and manifest.": _translations(
        "No hay espacio suficiente para la conversión MIDI y el manifiesto.", "Espace insuffisant pour la conversion MIDI et le manifeste.",
        "Nicht genügend freier Speicherplatz für die MIDI-Konvertierung und das Manifest.", "Spazio insufficiente per la conversione MIDI e il manifesto.",
        "Espaço insuficiente para a conversão MIDI e o manifesto.", "Недостатъчно свободно място за MIDI преобразуването и манифеста.",
        "Onvoldoende vrije ruimte voor de MIDI-conversie en het manifest.", "Za mało wolnego miejsca na konwersję MIDI i manifest.",
        "MIDI変換とマニフェストの空き容量が不足しています。", "MIDI 변환 및 매니페스트를 위한 공간이 부족합니다.", "没有足够空间存放 MIDI 转换文件及清单。",
    ),
    "Select the Mark IV data volume containing the songs folder.": _translations(
        "Selecciona el volumen de datos Mark IV que contiene la carpeta songs.", "Sélectionnez le volume de données Mark IV contenant le dossier songs.",
        "Wählen Sie das Mark-IV-Datenvolume mit dem Ordner songs.", "Seleziona il volume dati Mark IV contenente la cartella songs.",
        "Selecione o volume de dados Mark IV que contém a pasta songs.", "Изберете тома с данни на Mark IV, съдържащ папката songs.",
        "Selecteer het Mark IV-gegevensvolume met de map songs.", "Wybierz wolumin danych Mark IV zawierający folder songs.",
        "songsフォルダーを含むMark IVデータボリュームを選択してください。", "songs 폴더가 있는 Mark IV 데이터 볼륨을 선택하세요.", "请选择包含 songs 文件夹的 Mark IV 数据卷。",
    ),
    "The songs folder must be a real directory, not a symbolic link.": _translations(
        "La carpeta songs debe ser un directorio real, no un enlace simbólico.", "Le dossier songs doit être un véritable répertoire, pas un lien symbolique.",
        "Der Ordner songs muss ein echtes Verzeichnis sein, kein symbolischer Link.", "La cartella songs deve essere una directory reale, non un collegamento simbolico.",
        "A pasta songs deve ser um diretório real, não um link simbólico.", "Папката songs трябва да е истинска директория, а не символна връзка.",
        "De map songs moet een echte map zijn, geen symbolische koppeling.", "Folder songs musi być rzeczywistym katalogiem, a nie dowiązaniem symbolicznym.",
        "songsフォルダーにはシンボリックリンクではなく実際のディレクトリが必要です。", "songs 폴더는 심볼릭 링크가 아닌 실제 디렉터리여야 합니다.", "songs 文件夹必须是实际目录，而非符号链接。",
    ),
    "No music or album assets were found in this volume.": _translations(
        "No se encontraron archivos de música ni de álbumes en este volumen.", "Aucun fichier musical ni élément d’album trouvé sur ce volume.",
        "Auf diesem Volume wurden keine Musik- oder Albumdateien gefunden.", "Nessun file musicale o di album trovato in questo volume.",
        "Nenhum arquivo de música ou de álbum foi encontrado neste volume.", "В този том няма музикални файлове или материали за албуми.",
        "Geen muziek- of albumbestanden gevonden op dit volume.", "Na tym woluminie nie znaleziono muzyki ani plików albumów.",
        "このボリュームには音楽やアルバムのファイルが見つかりませんでした。", "이 볼륨에서 음악 또는 앨범 파일을 찾지 못했습니다.", "此卷中未找到音乐或专辑文件。",
    ),
    "The source is not an accessible directory: {path}": _translations(
        "El origen no es un directorio accesible: {path}", "La source n’est pas un répertoire accessible : {path}",
        "Die Quelle ist kein zugängliches Verzeichnis: {path}", "L'origine non è una directory accessibile: {path}",
        "A origem não é um diretório acessível: {path}", "Източникът не е достъпна директория: {path}",
        "De bron is geen toegankelijke map: {path}", "Źródło nie jest dostępnym katalogiem: {path}",
        "アクセス可能なディレクトリではありません: {path}", "원본이 접근 가능한 디렉터리가 아닙니다: {path}", "源不是可访问的目录：{path}",
    ),
    "{path} is not mounted. Mount its data partition read-only, then select the mounted folder. This program does not mount devices.": _translations(
        "{path} no está montado. Monta su partición de datos en modo de solo lectura y selecciona la carpeta montada. Este programa no monta dispositivos.",
        "{path} n’est pas monté. Montez sa partition de données en lecture seule, puis sélectionnez le dossier monté. Ce programme ne monte pas les périphériques.",
        "{path} ist nicht eingehängt. Hängen Sie die Datenpartition schreibgeschützt ein und wählen Sie den eingehängten Ordner. Dieses Programm hängt keine Geräte ein.",
        "{path} non è montato. Monta la partizione dati in sola lettura, poi seleziona la cartella montata. Questo programma non monta dispositivi.",
        "{path} não está montado. Monte a partição de dados como somente leitura e selecione a pasta montada. Este programa não monta dispositivos.",
        "{path} не е монтиран. Монтирайте дяла с данни само за четене и изберете монтираната папка. Тази програма не монтира устройства.",
        "{path} is niet aangekoppeld. Koppel de gegevenspartitie alleen-lezen aan en selecteer de aangekoppelde map. Dit programma koppelt geen apparaten aan.",
        "{path} nie jest zamontowany. Zamontuj partycję danych tylko do odczytu, a następnie wybierz zamontowany folder. Ten program nie montuje urządzeń.",
        "{path}はマウントされていません。データパーティションを読み取り専用でマウントし、そのフォルダーを選択してください。このプログラムはデバイスをマウントしません。",
        "{path}이(가) 마운트되지 않았습니다. 데이터 파티션을 읽기 전용으로 마운트한 뒤 해당 폴더를 선택하세요. 이 프로그램은 장치를 마운트하지 않습니다.",
        "{path} 尚未挂载。请以只读方式挂载其数据分区，然后选择已挂载的文件夹。本程序不会挂载设备。",
    ),
    "Source changed since the scan; scan again": _translations(
        "El origen cambió desde el análisis; vuelve a analizarlo", "La source a changé depuis l’analyse ; relancez l’analyse",
        "Die Quelle wurde seit dem Scan geändert; erneut scannen", "L'origine è cambiata dall'analisi; esegui una nuova analisi",
        "A origem mudou desde a análise; analise novamente", "Източникът е променен след сканирането; сканирайте отново",
        "De bron is gewijzigd sinds de scan; scan opnieuw", "Źródło zmieniło się od skanowania; przeskanuj ponownie",
        "スキャン後に元のファイルが変更されました。再スキャンしてください", "스캔 후 원본이 변경되었습니다. 다시 스캔하세요", "源在扫描后已更改，请重新扫描",
    ),
    "Source changed while it was being copied": _translations(
        "El origen cambió mientras se copiaba", "La source a changé pendant la copie",
        "Die Quelle wurde während des Kopierens geändert", "L'origine è cambiata durante la copia",
        "A origem mudou durante a cópia", "Източникът е променен по време на копирането",
        "De bron is gewijzigd tijdens het kopiëren", "Źródło zmieniło się podczas kopiowania",
        "コピー中に元のファイルが変更されました", "복사 중 원본이 변경되었습니다", "源在复制过程中已更改",
    ),
    "Source is no longer a regular file": _translations(
        "El origen ya no es un archivo normal", "La source n’est plus un fichier ordinaire", "Die Quelle ist keine reguläre Datei mehr",
        "L'origine non è più un file normale", "A origem não é mais um arquivo comum", "Източникът вече не е обикновен файл",
        "De bron is geen normaal bestand meer", "Źródło nie jest już zwykłym plikiem", "元のファイルが通常のファイルではなくなりました", "원본이 더 이상 일반 파일이 아닙니다", "源不再是普通文件",
    ),
    "Conversion source is no longer a regular file": _translations(
        "El origen de la conversión ya no es un archivo normal", "La source de conversion n’est plus un fichier ordinaire", "Die Konvertierungsquelle ist keine reguläre Datei mehr",
        "L'origine della conversione non è più un file normale", "A origem da conversão não é mais um arquivo comum", "Източникът за преобразуване вече не е обикновен файл",
        "De conversiebron is geen normaal bestand meer", "Źródło konwersji nie jest już zwykłym plikiem", "変換元が通常のファイルではなくなりました", "변환 원본이 더 이상 일반 파일이 아닙니다", "转换源不再是普通文件",
    ),
    "Destination already exists; refusing to overwrite it": _translations(
        "El destino ya existe; no se sobrescribirá", "La destination existe déjà ; elle ne sera pas écrasée", "Das Ziel existiert bereits; es wird nicht überschrieben",
        "La destinazione esiste già; non verrà sovrascritta", "O destino já existe; não será sobrescrito", "Местоназначението вече съществува; няма да бъде презаписано",
        "De bestemming bestaat al; deze wordt niet overschreven", "Miejsce docelowe już istnieje; nie zostanie nadpisane", "保存先が既に存在するため、上書きしません", "대상이 이미 존재하여 덮어쓰지 않습니다", "目标已存在，拒绝覆盖",
    ),
    "Original backup checksum changed before conversion": _translations(
        "La suma de comprobación del original de la copia cambió antes de la conversión", "La somme de contrôle de l’original sauvegardé a changé avant la conversion",
        "Die Prüfsumme der gesicherten Originaldatei wurde vor der Konvertierung geändert", "Il checksum dell'originale nel backup è cambiato prima della conversione",
        "A soma de verificação do original no backup mudou antes da conversão", "Контролната сума на архивирания оригинал е променена преди преобразуването",
        "De controlesom van het geback-upte origineel is gewijzigd vóór de conversie", "Suma kontrolna oryginału w kopii zapasowej zmieniła się przed konwersją",
        "変換前にバックアップ原本のチェックサムが変わりました", "변환 전에 백업 원본의 체크섬이 변경되었습니다", "转换前备份原始文件的校验和已更改",
    ),
    "SHA-256 read-back verification failed": _translations(
        "Falló la verificación SHA-256 tras la lectura", "Échec de la vérification SHA-256 après relecture", "SHA-256-Prüfung beim erneuten Lesen fehlgeschlagen",
        "Verifica SHA-256 dopo rilettura non riuscita", "Falha na verificação SHA-256 após releitura", "Неуспешна SHA-256 проверка при повторно четене",
        "SHA-256-controle na teruglezen mislukt", "Weryfikacja SHA-256 po ponownym odczycie nie powiodła się", "再読み込み時のSHA-256検証に失敗しました", "다시 읽은 데이터의 SHA-256 검증에 실패했습니다", "回读 SHA-256 验证失败",
    ),
    "Converted MIDI SHA-256 read-back verification failed": _translations(
        "Falló la verificación SHA-256 del MIDI convertido tras la lectura", "Échec de la vérification SHA-256 du MIDI converti après relecture", "SHA-256-Prüfung der konvertierten MIDI-Datei beim erneuten Lesen fehlgeschlagen",
        "Verifica SHA-256 del MIDI convertito dopo rilettura non riuscita", "Falha na verificação SHA-256 do MIDI convertido após releitura", "Неуспешна SHA-256 проверка на преобразувания MIDI при повторно четене",
        "SHA-256-controle van geconverteerde MIDI na teruglezen mislukt", "Weryfikacja SHA-256 przekonwertowanego MIDI po ponownym odczycie nie powiodła się",
        "変換したMIDIの再読み込み時のSHA-256検証に失敗しました", "변환된 MIDI를 다시 읽은 후 SHA-256 검증에 실패했습니다", "转换后的 MIDI 回读 SHA-256 验证失败",
    ),
    "File was not verified during backup": _translations(
        "El archivo no se verificó durante la copia", "Le fichier n’a pas été vérifié pendant la sauvegarde", "Die Datei wurde während der Sicherung nicht überprüft",
        "Il file non è stato verificato durante il backup", "O arquivo não foi verificado durante o backup", "Файлът не е проверен при архивирането",
        "Het bestand is niet gecontroleerd tijdens de back-up", "Plik nie został zweryfikowany podczas tworzenia kopii zapasowej", "バックアップ中にファイルが検証されませんでした", "백업 중 파일이 검증되지 않았습니다", "备份期间未验证此文件",
    ),
    "Original has no verified MIDI replacement": _translations(
        "El original no tiene un sustituto MIDI verificado", "L’original n’a pas de remplacement MIDI vérifié", "Für das Original gibt es keinen geprüften MIDI-Ersatz",
        "L'originale non ha un sostituto MIDI verificato", "O original não tem um substituto MIDI verificado", "Оригиналът няма проверен MIDI заместител",
        "Het origineel heeft geen gecontroleerde MIDI-vervanging", "Oryginał nie ma zweryfikowanego zamiennika MIDI", "原本を置き換える検証済みMIDIがありません", "원본을 대체할 검증된 MIDI가 없습니다", "原始文件没有经过验证的 MIDI 替代文件",
    ),
    "Size does not match": _translations(
        "El tamaño no coincide", "La taille ne correspond pas", "Die Größe stimmt nicht überein", "La dimensione non corrisponde", "O tamanho não corresponde", "Размерът не съвпада",
        "De grootte komt niet overeen", "Rozmiar się nie zgadza", "サイズが一致しません", "크기가 일치하지 않습니다", "大小不匹配",
    ),
    "SHA-256 checksum does not match": _translations(
        "La suma de comprobación SHA-256 no coincide", "La somme de contrôle SHA-256 ne correspond pas", "Die SHA-256-Prüfsumme stimmt nicht überein",
        "Il checksum SHA-256 non corrisponde", "A soma de verificação SHA-256 não corresponde", "Контролната сума SHA-256 не съвпада",
        "De SHA-256-controlesom komt niet overeen", "Suma kontrolna SHA-256 się nie zgadza", "SHA-256チェックサムが一致しません", "SHA-256 체크섬이 일치하지 않습니다", "SHA-256 校验和不匹配",
    ),
    "Backup is not complete (status: {status})": _translations(
        "La copia no está completa (estado: {status})", "La sauvegarde n’est pas terminée (état : {status})", "Die Sicherung ist unvollständig (Status: {status})",
        "Il backup non è completo (stato: {status})", "O backup não está completo (status: {status})", "Архивирането не е завършено (състояние: {status})",
        "De back-up is niet voltooid (status: {status})", "Kopia zapasowa jest niekompletna (stan: {status})", "バックアップが完了していません（状態: {status}）", "백업이 완료되지 않았습니다(상태: {status})", "备份未完成（状态：{status}）",
    ),
    "E-SEQ conversion failed: {error}": _translations(
        "Falló la conversión E-SEQ: {error}", "Échec de la conversion E-SEQ : {error}", "E-SEQ-Konvertierung fehlgeschlagen: {error}",
        "Conversione E-SEQ non riuscita: {error}", "Falha na conversão E-SEQ: {error}", "Неуспешно преобразуване на E-SEQ: {error}",
        "E-SEQ-conversie mislukt: {error}", "Konwersja E-SEQ nie powiodła się: {error}", "E-SEQ変換に失敗しました: {error}", "E-SEQ 변환에 실패했습니다: {error}", "E-SEQ 转换失败：{error}",
    ),
    "Could not remove converted backup original: {error}": _translations(
        "No se pudo eliminar el original convertido de la copia: {error}", "Impossible de supprimer l’original converti de la sauvegarde : {error}", "Das konvertierte Original in der Sicherung konnte nicht entfernt werden: {error}",
        "Impossibile rimuovere l'originale convertito dal backup: {error}", "Não foi possível remover o original convertido do backup: {error}", "Не може да се премахне преобразуваният оригинал от архива: {error}",
        "Het geconverteerde origineel in de back-up kon niet worden verwijderd: {error}", "Nie można usunąć przekonwertowanego oryginału z kopii zapasowej: {error}",
        "バックアップ内の変換済み原本を削除できませんでした: {error}", "백업에서 변환된 원본을 제거하지 못했습니다: {error}", "无法移除备份中已转换的原始文件：{error}",
    ),
    "Could not scan {path}: {error}": _translations(
        "No se pudo analizar {path}: {error}", "Impossible d’analyser {path} : {error}", "{path} konnte nicht gescannt werden: {error}",
        "Impossibile analizzare {path}: {error}", "Não foi possível analisar {path}: {error}", "Не може да се сканира {path}: {error}",
        "Kan {path} niet scannen: {error}", "Nie można przeskanować {path}: {error}", "{path}をスキャンできませんでした: {error}", "{path}을(를) 스캔하지 못했습니다: {error}", "无法扫描 {path}：{error}",
    ),
    "Could not inspect {path}: {error}": _translations(
        "No se pudo inspeccionar {path}: {error}", "Impossible d’inspecter {path} : {error}", "{path} konnte nicht untersucht werden: {error}",
        "Impossibile esaminare {path}: {error}", "Não foi possível inspecionar {path}: {error}", "Не може да се провери {path}: {error}",
        "Kan {path} niet inspecteren: {error}", "Nie można sprawdzić {path}: {error}", "{path}を検査できませんでした: {error}", "{path}을(를) 검사하지 못했습니다: {error}", "无法检查 {path}：{error}",
    ),
    "Could not read album index {path}: {error}": _translations(
        "No se pudo leer el índice del álbum {path}: {error}", "Impossible de lire l’index de l’album {path} : {error}", "Der Albumindex {path} konnte nicht gelesen werden: {error}",
        "Impossibile leggere l'indice dell'album {path}: {error}", "Não foi possível ler o índice do álbum {path}: {error}", "Не може да се прочете индексът на албума {path}: {error}",
        "Kan albumindex {path} niet lezen: {error}", "Nie można odczytać indeksu albumu {path}: {error}", "アルバム索引{path}を読み込めませんでした: {error}", "앨범 색인 {path}을(를) 읽지 못했습니다: {error}", "无法读取专辑索引 {path}：{error}",
    ),
    "Could not read song index {path}: {error}": _translations(
        "No se pudo leer el índice de canciones {path}: {error}", "Impossible de lire l’index des morceaux {path} : {error}", "Der Titelindex {path} konnte nicht gelesen werden: {error}",
        "Impossibile leggere l'indice dei brani {path}: {error}", "Não foi possível ler o índice de músicas {path}: {error}", "Не може да се прочете индексът на песните {path}: {error}",
        "Kan nummerindex {path} niet lezen: {error}", "Nie można odczytać indeksu utworów {path}: {error}", "曲の索引{path}を読み込めませんでした: {error}", "곡 색인 {path}을(를) 읽지 못했습니다: {error}", "无法读取曲目索引 {path}：{error}",
    ),
    "Could not read song title {path}: {error}": _translations(
        "No se pudo leer el título de la canción {path}: {error}", "Impossible de lire le titre du morceau {path} : {error}", "Der Songtitel {path} konnte nicht gelesen werden: {error}",
        "Impossibile leggere il titolo del brano {path}: {error}", "Não foi possível ler o título da música {path}: {error}", "Не може да се прочете заглавието на песента {path}: {error}",
        "Kan de titel van nummer {path} niet lezen: {error}", "Nie można odczytać tytułu utworu {path}: {error}", "曲名{path}を読み込めませんでした: {error}", "곡 제목 {path}을(를) 읽지 못했습니다: {error}", "无法读取曲目标题 {path}：{error}",
    ),
    "Skipped symbolic link: {path}": _translations(
        "Enlace simbólico omitido: {path}", "Lien symbolique ignoré : {path}", "Symbolischer Link übersprungen: {path}",
        "Collegamento simbolico ignorato: {path}", "Link simbólico ignorado: {path}", "Пропусната символна връзка: {path}",
        "Symbolische koppeling overgeslagen: {path}", "Pominięto dowiązanie symboliczne: {path}", "シンボリックリンクをスキップしました: {path}", "심볼릭 링크를 건너뛰었습니다: {path}", "已跳过符号链接：{path}",
    ),
    "Skipped non-regular file: {path}": _translations(
        "Archivo no normal omitido: {path}", "Fichier non ordinaire ignoré : {path}", "Nicht reguläre Datei übersprungen: {path}",
        "File non normale ignorato: {path}", "Arquivo não comum ignorado: {path}", "Пропуснат файл, който не е обикновен: {path}",
        "Niet-normaal bestand overgeslagen: {path}", "Pominięto plik inny niż zwykły: {path}", "通常ではないファイルをスキップしました: {path}", "일반 파일이 아닌 항목을 건너뛰었습니다: {path}", "已跳过非普通文件：{path}",
    ),
    "Unsafe relative path: {path}": _translations(
        "Ruta relativa insegura: {path}", "Chemin relatif non sécurisé : {path}", "Unsicherer relativer Pfad: {path}",
        "Percorso relativo non sicuro: {path}", "Caminho relativo inseguro: {path}", "Опасен относителен път: {path}",
        "Onveilig relatief pad: {path}", "Niebezpieczna ścieżka względna: {path}", "安全でない相対パス: {path}", "안전하지 않은 상대 경로: {path}", "不安全的相对路径：{path}",
    ),
    "Path escapes its root: {path}": _translations(
        "La ruta sale de su raíz: {path}", "Le chemin sort de sa racine : {path}", "Der Pfad verlässt sein Stammverzeichnis: {path}",
        "Il percorso esce dalla directory radice: {path}", "O caminho sai de sua raiz: {path}", "Пътят излиза извън кореновата директория: {path}",
        "Het pad valt buiten de hoofdmap: {path}", "Ścieżka wychodzi poza katalog główny: {path}", "パスがルートの外に出ています: {path}", "경로가 루트 밖으로 벗어납니다: {path}", "路径超出根目录：{path}",
    ),
    "Symbolic links are not followed: {path}": _translations(
        "No se siguen enlaces simbólicos: {path}", "Les liens symboliques ne sont pas suivis : {path}", "Symbolischen Links wird nicht gefolgt: {path}",
        "I collegamenti simbolici non vengono seguiti: {path}", "Links simbólicos não são seguidos: {path}", "Символните връзки не се следват: {path}",
        "Symbolische koppelingen worden niet gevolgd: {path}", "Dowiązania symboliczne nie są śledzone: {path}", "シンボリックリンクはたどりません: {path}", "심볼릭 링크를 따르지 않습니다: {path}", "不跟随符号链接：{path}",
    ),
    "Not a regular file: {path}": _translations(
        "No es un archivo normal: {path}", "Ce n’est pas un fichier ordinaire : {path}", "Keine reguläre Datei: {path}",
        "Non è un file normale: {path}", "Não é um arquivo comum: {path}", "Не е обикновен файл: {path}",
        "Geen normaal bestand: {path}", "To nie jest zwykły plik: {path}", "通常のファイルではありません: {path}", "일반 파일이 아닙니다: {path}", "不是普通文件：{path}",
    ),
    "Duplicate backup destination: {path}": _translations(
        "Destino de copia duplicado: {path}", "Destination de sauvegarde en double : {path}", "Doppeltes Sicherungsziel: {path}",
        "Destinazione di backup duplicata: {path}", "Destino de backup duplicado: {path}", "Дублирано местоназначение за архивиране: {path}",
        "Dubbele back-upbestemming: {path}", "Powielone miejsce docelowe kopii zapasowej: {path}", "バックアップ先が重複しています: {path}", "중복된 백업 대상: {path}", "重复的备份目标：{path}",
    ),
    "Reserved backup filename: {path}": _translations(
        "Nombre de archivo reservado para la copia: {path}", "Nom de fichier réservé pour la sauvegarde : {path}", "Reservierter Sicherungsdateiname: {path}",
        "Nome file riservato per il backup: {path}", "Nome de arquivo reservado para backup: {path}", "Запазено име на файл за архивиране: {path}",
        "Gereserveerde back-upbestandsnaam: {path}", "Zastrzeżona nazwa pliku kopii zapasowej: {path}", "予約済みのバックアップファイル名: {path}", "예약된 백업 파일 이름: {path}", "保留的备份文件名：{path}",
    ),
    "Unrecognized backup manifest": _translations(
        "Manifiesto de copia no reconocido", "Manifeste de sauvegarde non reconnu", "Unbekanntes Sicherungsmanifest", "Manifesto di backup non riconosciuto", "Manifesto de backup não reconhecido", "Неразпознат манифест на архива",
        "Onbekend back-upmanifest", "Nierozpoznany manifest kopii zapasowej", "バックアップマニフェストを認識できません", "백업 매니페스트를 인식할 수 없습니다", "无法识别备份清单",
    ),
    "Unrecognized backup derivative records": _translations(
        "Registros de archivos derivados de la copia no reconocidos", "Enregistrements des fichiers dérivés de sauvegarde non reconnus", "Unbekannte Einträge abgeleiteter Sicherungsdateien",
        "Record dei file derivati del backup non riconosciuti", "Registros de arquivos derivados do backup não reconhecidos", "Неразпознати записи за производни файлове на архива",
        "Onbekende records van afgeleide back-upbestanden", "Nierozpoznane wpisy plików pochodnych kopii zapasowej", "バックアップ派生ファイルの記録を認識できません", "백업 파생 파일 기록을 인식할 수 없습니다", "无法识别备份派生文件记录",
    ),
    "Unrecognized backup progress journal": _translations(
        "Registro de progreso de la copia no reconocido", "Journal de progression de sauvegarde non reconnu", "Unbekanntes Sicherungsfortschrittsprotokoll",
        "Registro di avanzamento del backup non riconosciuto", "Registro de progresso do backup não reconhecido", "Неразпознат дневник за напредъка на архива",
        "Onbekend back-upvoortgangslogboek", "Nierozpoznany dziennik postępu kopii zapasowej", "バックアップ進行状況のジャーナルを認識できません", "백업 진행 로그를 인식할 수 없습니다", "无法识别备份进度日志",
    ),
    "Invalid journal sequence": _translations(
        "Secuencia del registro no válida", "Séquence du journal invalide", "Ungültige Protokollreihenfolge", "Sequenza del registro non valida", "Sequência de registro inválida", "Невалидна последователност в дневника",
        "Ongeldige logboekvolgorde", "Nieprawidłowa kolejność dziennika", "ジャーナルの順序が無効です", "로그 순서가 잘못되었습니다", "日志顺序无效",
    ),
    "Invalid journal record": _translations(
        "Entrada del registro no válida", "Entrée du journal invalide", "Ungültiger Protokolleintrag", "Voce del registro non valida", "Entrada de registro inválida", "Невалиден запис в дневника",
        "Ongeldige logboekvermelding", "Nieprawidłowy wpis dziennika", "ジャーナルの記録が無効です", "로그 기록이 잘못되었습니다", "日志记录无效",
    ),
    "Invalid journal record index": _translations(
        "Índice de entrada del registro no válido", "Index d’entrée du journal invalide", "Ungültiger Index eines Protokolleintrags", "Indice della voce del registro non valido", "Índice de entrada de registro inválido", "Невалиден индекс на запис в дневника",
        "Ongeldige index van logboekvermelding", "Nieprawidłowy indeks wpisu dziennika", "ジャーナルの記録インデックスが無効です", "로그 기록 색인이 잘못되었습니다", "日志记录索引无效",
    ),
    "No readable Mark IV PostgreSQL database found; using management files and filenames.": _translations(
        "No se encontró una base de datos PostgreSQL Mark IV legible; se usarán archivos de gestión y nombres de archivo.",
        "Aucune base PostgreSQL Mark IV lisible trouvée ; utilisation des fichiers de gestion et des noms de fichiers.",
        "Keine lesbare Mark-IV-PostgreSQL-Datenbank gefunden; Verwaltungsdateien und Dateinamen werden verwendet.",
        "Nessun database PostgreSQL Mark IV leggibile trovato; vengono usati i file di gestione e i nomi dei file.",
        "Nenhum banco PostgreSQL Mark IV legível encontrado; serão usados arquivos de gerenciamento e nomes de arquivo.",
        "Не е намерена четима база PostgreSQL на Mark IV; използват се управляващи файлове и имена на файлове.",
        "Geen leesbare Mark IV PostgreSQL-database gevonden; beheerbestanden en bestandsnamen worden gebruikt.",
        "Nie znaleziono czytelnej bazy PostgreSQL Mark IV; używane są pliki zarządzania i nazwy plików.",
        "読み取り可能なMark IV PostgreSQLデータベースが見つからないため、管理ファイルとファイル名を使用します。",
        "읽을 수 있는 Mark IV PostgreSQL 데이터베이스가 없어 관리 파일과 파일 이름을 사용합니다.",
        "未找到可读的 Mark IV PostgreSQL 数据库，将使用管理文件和文件名。",
    ),
    "No Mark IV music tables found in the database; using management files and filenames.": _translations(
        "No se encontraron tablas de música Mark IV en la base de datos; se usarán archivos de gestión y nombres de archivo.",
        "Aucune table musicale Mark IV trouvée dans la base ; utilisation des fichiers de gestion et des noms de fichiers.",
        "Keine Mark-IV-Musiktabellen in der Datenbank gefunden; Verwaltungsdateien und Dateinamen werden verwendet.",
        "Nessuna tabella musicale Mark IV trovata nel database; vengono usati i file di gestione e i nomi dei file.",
        "Nenhuma tabela de músicas Mark IV encontrada no banco; serão usados arquivos de gerenciamento e nomes de arquivo.",
        "В базата не са намерени музикални таблици на Mark IV; използват се управляващи файлове и имена на файлове.",
        "Geen Mark IV-muziektabellen in de database gevonden; beheerbestanden en bestandsnamen worden gebruikt.",
        "Nie znaleziono tabel muzyki Mark IV w bazie; używane są pliki zarządzania i nazwy plików.",
        "データベースにMark IVの音楽テーブルが見つからないため、管理ファイルとファイル名を使用します。",
        "데이터베이스에 Mark IV 음악 테이블이 없어 관리 파일과 파일 이름을 사용합니다.",
        "数据库中未找到 Mark IV 音乐表，将使用管理文件和文件名。",
    ),
    "Database metadata could not be fully decoded: {error}. Using available management files and filenames.": _translations(
        "No se pudieron decodificar todos los metadatos de la base de datos: {error}. Se usarán los archivos de gestión y nombres de archivo disponibles.",
        "Impossible de décoder toutes les métadonnées de la base : {error}. Utilisation des fichiers de gestion et noms de fichiers disponibles.",
        "Datenbankmetadaten konnten nicht vollständig dekodiert werden: {error}. Verfügbare Verwaltungsdateien und Dateinamen werden verwendet.",
        "Impossibile decodificare tutti i metadati del database: {error}. Vengono usati i file di gestione e i nomi dei file disponibili.",
        "Não foi possível decodificar todos os metadados do banco: {error}. Serão usados os arquivos de gerenciamento e nomes de arquivo disponíveis.",
        "Метаданните на базата не могат да бъдат напълно декодирани: {error}. Използват се наличните управляващи файлове и имена на файлове.",
        "Databasemetadata kon niet volledig worden gedecodeerd: {error}. Beschikbare beheerbestanden en bestandsnamen worden gebruikt.",
        "Nie można w pełni odkodować metadanych bazy: {error}. Używane są dostępne pliki zarządzania i nazwy plików.",
        "データベースのメタデータを完全にはデコードできませんでした: {error}。利用可能な管理ファイルとファイル名を使用します。",
        "데이터베이스 메타데이터를 완전히 디코딩하지 못했습니다: {error}. 사용 가능한 관리 파일과 파일 이름을 사용합니다.",
        "无法完整解码数据库元数据：{error}。将使用可用的管理文件和文件名。",
    ),
    "Ignored unsafe database filename in {category}.": _translations(
        "Se ignoró un nombre de archivo de base de datos inseguro en {category}.", "Nom de fichier de base de données non sécurisé ignoré dans {category}.",
        "Unsicherer Datenbankdateiname in {category} ignoriert.", "Nome file di database non sicuro ignorato in {category}.",
        "Nome de arquivo inseguro do banco ignorado em {category}.", "Опасно име на файл в базата е пропуснато в {category}.",
        "Onveilige databasebestandsnaam in {category} genegeerd.", "Zignorowano niebezpieczną nazwę pliku bazy w {category}.",
        "{category}内の安全でないデータベースファイル名を無視しました。", "{category}에서 안전하지 않은 데이터베이스 파일 이름을 무시했습니다.", "已忽略 {category} 中不安全的数据库文件名。",
    ),
    "Only the inspected PostgreSQL 7.3 disk layout is supported": _translations(
        "Solo se admite la disposición de disco PostgreSQL 7.3 inspeccionada", "Seule la structure de disque PostgreSQL 7.3 inspectée est prise en charge",
        "Nur das untersuchte PostgreSQL-7.3-Datenträgerlayout wird unterstützt", "È supportata solo la struttura del disco PostgreSQL 7.3 esaminata",
        "Somente o layout de disco PostgreSQL 7.3 inspecionado é compatível", "Поддържа се само проверената дискова структура на PostgreSQL 7.3",
        "Alleen de onderzochte PostgreSQL 7.3-schijfindeling wordt ondersteund", "Obsługiwana jest tylko sprawdzona struktura dyskowa PostgreSQL 7.3",
        "確認済みのPostgreSQL 7.3ディスク構造のみ対応しています", "검사된 PostgreSQL 7.3 디스크 구조만 지원됩니다", "仅支持已检验的 PostgreSQL 7.3 磁盘布局",
    ),
    "Disklavier music": _translations(
        "Música Disklavier", "Musique Disklavier", "Disklavier-Musik", "Musica Disklavier", "Música Disklavier", "Музика за Disklavier",
        "Disklavier-muziek", "Muzyka Disklavier", "Disklavierの音楽", "Disklavier 음악", "Disklavier 音乐",
    ),
    "Refusing metadata symlink: {path}": _translations(
        "Se rechazó un enlace simbólico de metadatos: {path}", "Lien symbolique de métadonnées refusé : {path}", "Symbolischer Metadatenlink abgelehnt: {path}",
        "Collegamento simbolico ai metadati rifiutato: {path}", "Link simbólico de metadados recusado: {path}", "Отказана символна връзка към метаданни: {path}",
        "Symbolische koppeling naar metadata geweigerd: {path}", "Odrzucono dowiązanie symboliczne metadanych: {path}", "メタデータのシンボリックリンクを拒否しました: {path}", "메타데이터 심볼릭 링크를 거부했습니다: {path}", "已拒绝元数据符号链接：{path}",
    ),
    "Refusing symlinked database storage directory": _translations(
        "Se rechazó el directorio de la base de datos por ser un enlace simbólico", "Répertoire de stockage de base de données symbolique refusé", "Symbolischer Link als Datenbankspeicherverzeichnis abgelehnt",
        "Directory di archiviazione del database rifiutata perché è un collegamento simbolico", "Diretório de armazenamento do banco recusado por ser um link simbólico", "Отказана директория за базата данни, която е символна връзка",
        "Symbolische koppeling als databaseopslagmap geweigerd", "Odrzucono katalog bazy danych będący dowiązaniem symbolicznym", "シンボリックリンクのデータベース保存ディレクトリを拒否しました", "심볼릭 링크인 데이터베이스 저장 디렉터리를 거부했습니다", "已拒绝作为符号链接的数据库存储目录",
    ),
    "Cannot establish transaction status: {path}": _translations(
        "No se puede determinar el estado de la transacción: {path}", "Impossible de déterminer l’état de la transaction : {path}", "Transaktionsstatus kann nicht ermittelt werden: {path}",
        "Impossibile determinare lo stato della transazione: {path}", "Não foi possível determinar o status da transação: {path}", "Не може да се определи състоянието на транзакцията: {path}",
        "Kan transactiestatus niet bepalen: {path}", "Nie można ustalić stanu transakcji: {path}", "トランザクションの状態を確認できません: {path}", "트랜잭션 상태를 확인할 수 없습니다: {path}", "无法确定事务状态：{path}",
    ),
    "Transaction status {id} lies beyond the available log": _translations(
        "El estado de la transacción {id} está fuera del registro disponible", "L’état de la transaction {id} dépasse le journal disponible", "Der Status der Transaktion {id} liegt außerhalb des verfügbaren Protokolls",
        "Lo stato della transazione {id} è oltre il registro disponibile", "O status da transação {id} está fora do registro disponível", "Състоянието на транзакция {id} е извън наличния дневник",
        "De status van transactie {id} valt buiten het beschikbare logboek", "Stan transakcji {id} wykracza poza dostępny dziennik", "トランザクション{id}の状態が利用可能なログの範囲外です", "트랜잭션 {id}의 상태가 사용 가능한 로그 범위를 벗어납니다", "事务 {id} 的状态超出可用日志范围",
    ),
    "Missing database relation {name}": _translations(
        "Falta la relación de base de datos {name}", "Relation de base de données manquante : {name}", "Datenbankrelation {name} fehlt", "Relazione del database {name} mancante", "Relação {name} ausente no banco de dados", "Липсва релация {name} в базата данни",
        "Databaserelatie {name} ontbreekt", "Brak relacji bazy danych {name}", "データベースリレーション{name}がありません", "데이터베이스 릴레이션 {name}이(가) 없습니다", "缺少数据库关系 {name}",
    ),
    "Refusing database relation symlink {path}": _translations(
        "Se rechazó el enlace simbólico de relación de base de datos {path}", "Lien symbolique de relation de base de données refusé : {path}", "Symbolischer Link für Datenbankrelation abgelehnt: {path}",
        "Collegamento simbolico alla relazione del database rifiutato: {path}", "Link simbólico de relação do banco recusado: {path}", "Отказана символна връзка към релация в базата данни: {path}",
        "Symbolische koppeling naar databaserelatie geweigerd: {path}", "Odrzucono dowiązanie symboliczne relacji bazy danych {path}", "データベースリレーションのシンボリックリンクを拒否しました: {path}", "데이터베이스 릴레이션 심볼릭 링크 {path}을(를) 거부했습니다", "已拒绝数据库关系符号链接 {path}",
    ),
    "Truncated database page in {path}": _translations(
        "Página de base de datos truncada en {path}", "Page de base de données tronquée dans {path}", "Abgeschnittene Datenbankseite in {path}", "Pagina del database troncata in {path}", "Página de banco truncada em {path}", "Отрязана страница на базата данни в {path}",
        "Afgebroken databasepagina in {path}", "Ucięta strona bazy danych w {path}", "{path}内のデータベースページが切り詰められています", "{path}의 데이터베이스 페이지가 잘렸습니다", "{path} 中的数据库页被截断",
    ),
    "Unsupported/corrupt PostgreSQL page in {path}": _translations(
        "Página PostgreSQL no compatible o dañada en {path}", "Page PostgreSQL non prise en charge ou corrompue dans {path}", "Nicht unterstützte oder beschädigte PostgreSQL-Seite in {path}",
        "Pagina PostgreSQL non supportata o danneggiata in {path}", "Página PostgreSQL incompatível ou corrompida em {path}", "Неподдържана или повредена страница на PostgreSQL в {path}",
        "Niet-ondersteunde of beschadigde PostgreSQL-pagina in {path}", "Nieobsługiwana lub uszkodzona strona PostgreSQL w {path}", "{path}内のPostgreSQLページが未対応または破損しています", "{path}의 PostgreSQL 페이지가 지원되지 않거나 손상되었습니다", "{path} 中的 PostgreSQL 页不受支持或已损坏",
    ),
    "Invalid tuple pointer in {path}": _translations(
        "Puntero de tupla no válido en {path}", "Pointeur de tuple invalide dans {path}", "Ungültiger Tupelzeiger in {path}", "Puntatore di tupla non valido in {path}", "Ponteiro de tupla inválido em {path}", "Невалиден указател към кортеж в {path}",
        "Ongeldige tuplepointer in {path}", "Nieprawidłowy wskaźnik krotki w {path}", "{path}内のタプルポインターが無効です", "{path}의 튜플 포인터가 잘못되었습니다", "{path} 中的元组指针无效",
    ),
    "Invalid tuple header in {path}": _translations(
        "Cabecera de tupla no válida en {path}", "En-tête de tuple invalide dans {path}", "Ungültiger Tupelkopf in {path}", "Intestazione di tupla non valida in {path}", "Cabeçalho de tupla inválido em {path}", "Невалидна заглавна част на кортеж в {path}",
        "Ongeldige tupleheader in {path}", "Nieprawidłowy nagłówek krotki w {path}", "{path}内のタプルヘッダーが無効です", "{path}의 튜플 헤더가 잘못되었습니다", "{path} 中的元组头无效",
    ),
    "Unrecognized pg_class catalog layout": _translations(
        "Disposición del catálogo pg_class no reconocida", "Structure du catalogue pg_class non reconnue", "Unbekannte Struktur des pg_class-Katalogs", "Struttura del catalogo pg_class non riconosciuta", "Estrutura do catálogo pg_class não reconhecida", "Неразпозната структура на каталога pg_class",
        "Onbekende indeling van de pg_class-catalogus", "Nierozpoznana struktura katalogu pg_class", "pg_classカタログの構造を認識できません", "pg_class 카탈로그 구조를 인식할 수 없습니다", "无法识别 pg_class 目录布局",
    ),
    "Unrecognized pg_attribute catalog layout": _translations(
        "Disposición del catálogo pg_attribute no reconocida", "Structure du catalogue pg_attribute non reconnue", "Unbekannte Struktur des pg_attribute-Katalogs", "Struttura del catalogo pg_attribute non riconosciuta", "Estrutura do catálogo pg_attribute não reconhecida", "Неразпозната структура на каталога pg_attribute",
        "Onbekende indeling van de pg_attribute-catalogus", "Nierozpoznana struktura katalogu pg_attribute", "pg_attributeカタログの構造を認識できません", "pg_attribute 카탈로그 구조를 인식할 수 없습니다", "无法识别 pg_attribute 目录布局",
    ),
    "Ambiguous live table {name}": _translations(
        "Tabla activa ambigua: {name}", "Table active ambiguë : {name}", "Mehrdeutige aktive Tabelle {name}", "Tabella attiva ambigua: {name}", "Tabela ativa ambígua: {name}", "Нееднозначна активна таблица {name}",
        "Dubbelzinnige actieve tabel {name}", "Niejednoznaczna aktywna tabela {name}", "アクティブなテーブル{name}が一意ではありません", "활성 테이블 {name}이(가) 모호합니다", "活动表 {name} 存在歧义",
    ),
    "Duplicate active database attribute": _translations(
        "Atributo activo de base de datos duplicado", "Attribut actif de base de données en double", "Doppeltes aktives Datenbankattribut", "Attributo attivo del database duplicato", "Atributo ativo do banco duplicado", "Дублиран активен атрибут на базата данни",
        "Dubbel actief databaseattribuut", "Powielony aktywny atrybut bazy danych", "アクティブなデータベース属性が重複しています", "활성 데이터베이스 속성이 중복되었습니다", "活动数据库属性重复",
    ),
    "Incomplete table schema in pg_attribute": _translations(
        "Esquema de tabla incompleto en pg_attribute", "Schéma de table incomplet dans pg_attribute", "Unvollständiges Tabellenschema in pg_attribute", "Schema della tabella incompleto in pg_attribute", "Esquema de tabela incompleto em pg_attribute", "Непълна схема на таблица в pg_attribute",
        "Onvolledig tabelschema in pg_attribute", "Niekompletny schemat tabeli w pg_attribute", "pg_attribute内のテーブルスキーマが不完全です", "pg_attribute의 테이블 스키마가 불완전합니다", "pg_attribute 中的表结构不完整",
    ),
    "Tuple has more fields than its catalog schema": _translations(
        "La tupla tiene más campos que su esquema de catálogo", "Le tuple contient plus de champs que son schéma de catalogue", "Das Tupel hat mehr Felder als sein Katalogschema", "La tupla ha più campi del relativo schema di catalogo", "A tupla tem mais campos que seu esquema de catálogo", "Кортежът има повече полета от схемата на каталога",
        "De tuple heeft meer velden dan het catalogusschema", "Krotka ma więcej pól niż jej schemat katalogu", "タプルのフィールド数がカタログスキーマより多くなっています", "튜플의 필드 수가 카탈로그 스키마보다 많습니다", "元组字段数超过其目录结构定义",
    ),
    "Unknown attribute alignment {alignment}": _translations(
        "Alineación de atributo desconocida: {alignment}", "Alignement d’attribut inconnu : {alignment}", "Unbekannte Attributausrichtung {alignment}", "Allineamento di attributo sconosciuto: {alignment}", "Alinhamento de atributo desconhecido: {alignment}", "Неизвестно подравняване на атрибут {alignment}",
        "Onbekende attribuutuitlijning {alignment}", "Nieznane wyrównanie atrybutu {alignment}", "不明な属性アラインメント: {alignment}", "알 수 없는 속성 정렬: {alignment}", "未知属性对齐方式 {alignment}",
    ),
    "Truncated variable-length attribute": _translations(
        "Atributo de longitud variable truncado", "Attribut de longueur variable tronqué", "Abgeschnittenes Attribut variabler Länge", "Attributo a lunghezza variabile troncato", "Atributo de comprimento variável truncado", "Отрязан атрибут с променлива дължина",
        "Afgebroken attribuut met variabele lengte", "Ucięty atrybut zmiennej długości", "可変長属性が切り詰められています", "가변 길이 속성이 잘렸습니다", "变长属性被截断",
    ),
    "Invalid variable-length attribute": _translations(
        "Atributo de longitud variable no válido", "Attribut de longueur variable invalide", "Ungültiges Attribut variabler Länge", "Attributo a lunghezza variabile non valido", "Atributo de comprimento variável inválido", "Невалиден атрибут с променлива дължина",
        "Ongeldig attribuut met variabele lengte", "Nieprawidłowy atrybut zmiennej długości", "可変長属性が無効です", "가변 길이 속성이 잘못되었습니다", "变长属性无效",
    ),
    "Truncated fixed-length attribute": _translations(
        "Atributo de longitud fija truncado", "Attribut de longueur fixe tronqué", "Abgeschnittenes Attribut fester Länge", "Attributo a lunghezza fissa troncato", "Atributo de comprimento fixo truncado", "Отрязан атрибут с фиксирана дължина",
        "Afgebroken attribuut met vaste lengte", "Ucięty atrybut stałej długości", "固定長属性が切り詰められています", "고정 길이 속성이 잘렸습니다", "定长属性被截断",
    ),
    "Unsupported database type for {name}": _translations(
        "Tipo de base de datos no compatible para {name}", "Type de base de données non pris en charge pour {name}", "Nicht unterstützter Datenbanktyp für {name}", "Tipo di database non supportato per {name}", "Tipo de banco não compatível para {name}", "Неподдържан тип данни в базата за {name}",
        "Niet-ondersteund databasetype voor {name}", "Nieobsługiwany typ bazy danych dla {name}", "{name}のデータベース型は未対応です", "{name}의 데이터베이스 형식이 지원되지 않습니다", "{name} 的数据库类型不受支持",
    ),
    "No schema for {name}": _translations(
        "No hay esquema para {name}", "Aucun schéma pour {name}", "Kein Schema für {name}", "Nessuno schema per {name}", "Nenhum esquema para {name}", "Липсва схема за {name}",
        "Geen schema voor {name}", "Brak schematu dla {name}", "{name}のスキーマがありません", "{name}의 스키마가 없습니다", "缺少 {name} 的结构定义",
    ),
}
