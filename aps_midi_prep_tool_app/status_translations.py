"""Localized batch-operation results, kept separate from filenames and logs."""

_LANGUAGES = ("es", "fr", "de", "it", "pt-BR", "bg", "nl", "pl", "ja", "ko", "zh-Hans")
_ROWS = {
    "Added {added_count} file(s).": (
        "Se añadieron {added_count} archivos.", "{added_count} fichier(s) ajouté(s).", "{added_count} Datei(en) hinzugefügt.", "Aggiunti {added_count} file.", "{added_count} arquivo(s) adicionado(s).", "Добавени файлове: {added_count}.", "{added_count} bestand(en) toegevoegd.", "Dodano plików: {added_count}.", "{added_count} 個のファイルを追加しました。", "파일 {added_count}개를 추가했습니다.", "已添加 {added_count} 个文件。",
    ),
    "Backup is enabled; Save will keep copies with the old filenames.": (
        "La copia de seguridad está activada; Guardar conservará copias con los nombres anteriores.", "La sauvegarde est activée ; Enregistrer conservera des copies avec les anciens noms.", "Die Sicherung ist aktiviert; beim Speichern bleiben Kopien mit den bisherigen Dateinamen erhalten.", "Il backup è attivo; Salva conserverà copie con i vecchi nomi.", "O backup está ativado; Salvar manterá cópias com os nomes antigos.", "Резервното копиране е включено; Запис ще запази копия със старите имена.", "Back-ups zijn ingeschakeld; Opslaan bewaart kopieën met de oude bestandsnamen.", "Kopie zapasowe są włączone; Zapisz zachowa kopie pod starymi nazwami.", "バックアップが有効です。「保存」で元のファイル名のコピーを残します。", "백업이 활성화되어 있습니다. 저장 시 이전 파일 이름으로 된 복사본을 보관합니다.", "备份已启用；保存时将保留使用原文件名的副本。",
    ),
    "Binary sustain transitions were softened; existing continuous pedal streams were preserved.": (
        "Se suavizaron las transiciones del pedal de sostenido binario; se conservaron los datos continuos existentes del pedal.", "Les transitions binaires de la pédale de sustain ont été adoucies ; les données continues existantes ont été conservées.", "Binäre Sustain-Übergänge wurden geglättet; vorhandene kontinuierliche Pedaldaten blieben erhalten.", "Le transizioni binarie del pedale sustain sono state attenuate; i dati continui esistenti sono stati conservati.", "As transições binárias do pedal de sustentação foram suavizadas; os dados contínuos existentes foram preservados.", "Двоичните преходи на десния педал са изгладени; съществуващите непрекъснати данни за педала са запазени.", "Binaire sustainovergangen zijn verzacht; bestaande continue pedaalgegevens zijn behouden.", "Złagodzono binarne przejścia pedału sustain; zachowano istniejące ciągłe dane pedału.", "二値サステインの切り替えを滑らかにしました。既存の連続ペダルデータは保持しています。", "이진 서스테인 전환을 부드럽게 했으며 기존 연속 페달 데이터는 보존했습니다.", "已平滑二值延音踏板的切换；保留了现有的连续踏板数据。",
    ),
    "Combined instruments on MIDI channel 1 using Acoustic Grand Piano.": (
        "Se combinaron los instrumentos en el canal MIDI 1 con piano de cola acústico.", "Les instruments ont été réunis sur le canal MIDI 1 avec un piano à queue acoustique.", "Instrumente wurden auf MIDI-Kanal 1 mit akustischem Flügel zusammengeführt.", "Gli strumenti sono stati uniti sul canale MIDI 1 usando il pianoforte a coda acustico.", "Os instrumentos foram combinados no canal MIDI 1 usando piano de cauda acústico.", "Инструментите са обединени в MIDI канал 1 с акустичен роял.", "Instrumenten zijn samengevoegd op MIDI-kanaal 1 met akoestische vleugel.", "Połączono instrumenty na kanale MIDI 1 z brzmieniem fortepianu akustycznego.", "アコースティックグランドピアノで楽器を MIDI チャンネル 1 に統合しました。", "어쿠스틱 그랜드 피아노를 사용하여 MIDI 채널 1로 악기를 통합했습니다.", "已使用原声大钢琴音色将乐器合并到 MIDI 通道 1。",
    ),
    'Converted {converted_count} E-SEQ file(s) to MIDI and left {source_mode_name}.\nCurrent context moved to: "{export_dir}"': (
        'Se convirtieron {converted_count} archivos E-SEQ a MIDI y se salió de {source_mode_name}.\nLa carpeta actual es: "{export_dir}"',
        '{converted_count} fichier(s) E-SEQ converti(s) en MIDI et sortie de {source_mode_name}.\nDossier actuel : « {export_dir} »',
        '{converted_count} E-SEQ-Datei(en) in MIDI konvertiert und {source_mode_name} verlassen.\nAktueller Ordner: „{export_dir}“',
        'Convertiti {converted_count} file E-SEQ in MIDI e uscita da {source_mode_name}.\nCartella attuale: "{export_dir}"',
        '{converted_count} arquivo(s) E-SEQ convertido(s) para MIDI e saída de {source_mode_name}.\nPasta atual: "{export_dir}"',
        'Преобразувани E-SEQ файлове в MIDI: {converted_count}; излизане от {source_mode_name}.\nТекуща папка: „{export_dir}“',
        '{converted_count} E-SEQ-bestand(en) naar MIDI geconverteerd en {source_mode_name} verlaten.\nHuidige map: "{export_dir}"',
        'Przekonwertowano {converted_count} plików E-SEQ na MIDI i opuszczono {source_mode_name}.\nBieżący folder: „{export_dir}”',
        '{converted_count} 個の E-SEQ ファイルを MIDI に変換し、{source_mode_name} を終了しました。\n現在のフォルダー: 「{export_dir}」',
        'E-SEQ 파일 {converted_count}개를 MIDI로 변환하고 {source_mode_name}에서 나왔습니다.\n현재 폴더: "{export_dir}"',
        '已将 {converted_count} 个 E-SEQ 文件转换为 MIDI，并退出{source_mode_name}。\n当前文件夹：“{export_dir}”',
    ),
    "Converted {converted_count} MIDI file(s) from {slot_count} V50/SY77 sequence(s) in {source_name} with routed channels and program changes.\nThe source files were not modified. Use Save As to choose a permanent folder.": (
        "Se crearon {converted_count} archivos MIDI a partir de {slot_count} secuencias V50/SY77 en {source_name}, con canales asignados y cambios de programa.\nNo se modificaron los archivos originales. Usa Guardar como para elegir una carpeta permanente.",
        "{converted_count} fichier(s) MIDI créé(s) à partir de {slot_count} séquence(s) V50/SY77 dans {source_name}, avec affectation des canaux et changements de programme.\nLes fichiers sources n'ont pas été modifiés. Utilisez Enregistrer sous pour choisir un dossier permanent.",
        "{converted_count} MIDI-Datei(en) aus {slot_count} V50/SY77-Sequenz(en) in {source_name} mit Kanalzuordnung und Programmwechseln erstellt.\nDie Quelldateien wurden nicht verändert. Wählen Sie mit Speichern unter einen dauerhaften Ordner.",
        "Creati {converted_count} file MIDI da {slot_count} sequenze V50/SY77 in {source_name}, con assegnazione dei canali e cambi di programma.\nI file originali non sono stati modificati. Usa Salva con nome per scegliere una cartella permanente.",
        "{converted_count} arquivo(s) MIDI criado(s) a partir de {slot_count} sequência(s) V50/SY77 em {source_name}, com canais atribuídos e mudanças de programa.\nOs arquivos de origem não foram modificados. Use Salvar como para escolher uma pasta permanente.",
        "Създадени MIDI файлове: {converted_count}, от {slot_count} V50/SY77 секвенции в {source_name}, с насочени канали и смени на инструменти.\nОригиналните файлове не са променени. Използвайте Запис като, за да изберете постоянна папка.",
        "{converted_count} MIDI-bestand(en) gemaakt van {slot_count} V50/SY77-sequentie(s) in {source_name}, met kanaaltoewijzing en programmawisselingen.\nDe bronbestanden zijn niet gewijzigd. Kies met Opslaan als een permanente map.",
        "Utworzono {converted_count} plików MIDI z {slot_count} sekwencji V50/SY77 w {source_name}, z przypisaniem kanałów i zmianami programów.\nPliki źródłowe nie zostały zmienione. Użyj Zapisz jako, aby wybrać docelowy folder.",
        "{source_name} の {slot_count} 個の V50/SY77 シーケンスから、チャンネル割り当てとプログラムチェンジを含む {converted_count} 個の MIDI ファイルを作成しました。\n元のファイルは変更していません。「別名で保存」で保存先フォルダーを選択してください。",
        "{source_name}의 V50/SY77 시퀀스 {slot_count}개에서 채널 할당 및 프로그램 변경이 포함된 MIDI 파일 {converted_count}개를 만들었습니다.\n원본 파일은 변경되지 않았습니다. 다른 이름으로 저장을 사용하여 보관할 폴더를 선택하세요.",
        "已从 {source_name} 中的 {slot_count} 个 V50/SY77 序列创建 {converted_count} 个 MIDI 文件，包含通道分配和音色切换。\n源文件未修改。请使用“另存为”选择永久保存的文件夹。",
    ),
    "Decoded {count} PianoDisc System 3 song(s) from {source_name}.\nThe source image was not modified. Use Save As to choose a permanent folder.": (
        "Se decodificaron {count} canciones de PianoDisc System 3 de {source_name}.\nNo se modificó la imagen original. Usa Guardar como para elegir una carpeta permanente.",
        "{count} morceau(x) PianoDisc System 3 décodé(s) depuis {source_name}.\nL'image source n'a pas été modifiée. Utilisez Enregistrer sous pour choisir un dossier permanent.",
        "{count} PianoDisc-System-3-Stück(e) aus {source_name} dekodiert.\nDas Quell-Image wurde nicht verändert. Wählen Sie mit Speichern unter einen dauerhaften Ordner.",
        "Decodificati {count} brani PianoDisc System 3 da {source_name}.\nL'immagine originale non è stata modificata. Usa Salva con nome per scegliere una cartella permanente.",
        "{count} música(s) PianoDisc System 3 decodificada(s) de {source_name}.\nA imagem de origem não foi modificada. Use Salvar como para escolher uma pasta permanente.",
        "Декодирани песни PianoDisc System 3 от {source_name}: {count}.\nОригиналният образ не е променен. Използвайте Запис като, за да изберете постоянна папка.",
        "{count} PianoDisc System 3-nummer(s) uit {source_name} gedecodeerd.\nDe bronimage is niet gewijzigd. Kies met Opslaan als een permanente map.",
        "Odczytano {count} utworów PianoDisc System 3 z {source_name}.\nObraz źródłowy nie został zmieniony. Użyj Zapisz jako, aby wybrać docelowy folder.",
        "{source_name} から {count} 曲の PianoDisc System 3 データをデコードしました。\n元のイメージは変更していません。「別名で保存」で保存先フォルダーを選択してください。",
        "{source_name}에서 PianoDisc System 3 곡 {count}개를 디코딩했습니다.\n원본 이미지는 변경되지 않았습니다. 다른 이름으로 저장을 사용하여 보관할 폴더를 선택하세요.",
        "已从 {source_name} 解码 {count} 首 PianoDisc System 3 乐曲。\n源映像未修改。请使用“另存为”选择永久保存的文件夹。",
    ),
    "Drop cancelled.": (
        "Se canceló la importación al arrastrar.", "Importation par glisser-déposer annulée.", "Import per Drag-and-drop abgebrochen.", "Importazione tramite trascinamento annullata.", "Importação por arrastar e soltar cancelada.", "Добавянето чрез плъзгане е отменено.", "Importeren via slepen geannuleerd.", "Anulowano importowanie przez przeciągnięcie.", "ドラッグ＆ドロップの取り込みをキャンセルしました。", "끌어서 놓기 가져오기가 취소되었습니다.", "已取消拖放导入。",
    ),
    "Estimated free space after pending additions: {size}.": (
        "Espacio libre estimado tras añadir los archivos pendientes: {size}.", "Espace libre estimé après les ajouts en attente : {size}.", "Geschätzter freier Speicher nach den vorgemerkten Ergänzungen: {size}.", "Spazio libero stimato dopo le aggiunte in sospeso: {size}.", "Espaço livre estimado após as adições pendentes: {size}.", "Очаквано свободно място след чакащите добавяния: {size}.", "Geschatte vrije ruimte na de geplande toevoegingen: {size}.", "Szacowane wolne miejsce po dodaniu oczekujących plików: {size}.", "保留中のファイル追加後の推定空き容量: {size}。", "대기 중인 파일 추가 후 예상 여유 공간: {size}.", "完成待处理的文件添加后的预计可用空间：{size}。",
    ),
    "Estimated free space after pending changes: {size}.": (
        "Espacio libre estimado tras los cambios pendientes: {size}.", "Espace libre estimé après les modifications en attente : {size}.", "Geschätzter freier Speicher nach den vorgemerkten Änderungen: {size}.", "Spazio libero stimato dopo le modifiche in sospeso: {size}.", "Espaço livre estimado após as alterações pendentes: {size}.", "Очаквано свободно място след чакащите промени: {size}.", "Geschatte vrije ruimte na de geplande wijzigingen: {size}.", "Szacowane wolne miejsce po oczekujących zmianach: {size}.", "保留中の変更後の推定空き容量: {size}。", "대기 중인 변경 후 예상 여유 공간: {size}.", "完成待保存更改后的预计可用空间：{size}。",
    ),
    "Loaded {filename}.": (
        "Se cargó {filename}.", "{filename} chargé.", "{filename} geladen.", "Caricato {filename}.", "{filename} carregado.", "Зареден е {filename}.", "{filename} geladen.", "Wczytano {filename}.", "{filename} を読み込みました。", "{filename}을(를) 불러왔습니다.", "已加载 {filename}。",
    ),
    "Named {count} MIDI file(s) from their track numbers and song titles.": (
        "Se asignaron nombres a {count} archivos MIDI a partir de sus números de pista y títulos.", "{count} fichier(s) MIDI nommé(s) d'après leurs numéros de piste et titres.", "{count} MIDI-Datei(en) anhand von Spurnummern und Songtiteln benannt.", "Assegnati nomi a {count} file MIDI in base ai numeri di traccia e ai titoli dei brani.", "{count} arquivo(s) MIDI nomeado(s) pelos números das faixas e títulos das músicas.", "Наименувани MIDI файлове по номерата на записите и заглавията на песните: {count}.", "{count} MIDI-bestand(en) benoemd op basis van tracknummers en songtitels.", "Nadano nazwy {count} plikom MIDI na podstawie numerów ścieżek i tytułów utworów.", "{count} 個の MIDI ファイルにトラック番号と曲名から名前を付けました。", "트랙 번호와 곡 제목으로 MIDI 파일 {count}개의 이름을 지정했습니다.", "已按曲目编号和乐曲标题为 {count} 个 MIDI 文件命名。",
    ),
}
_ROWS.update({
    "Queued XF removal for {changed_count} MIDI file(s).": (
        "Se preparó la eliminación de XF en {changed_count} archivos MIDI.", "Suppression XF mise en attente pour {changed_count} fichier(s) MIDI.", "XF-Entfernung für {changed_count} MIDI-Datei(en) vorgemerkt.", "Rimozione XF in coda per {changed_count} file MIDI.", "Remoção de XF na fila para {changed_count} arquivo(s) MIDI.", "Премахване на XF е поставено на опашка за {changed_count} MIDI файла.", "XF-verwijdering klaargezet voor {changed_count} MIDI-bestand(en).", "Dodano do kolejki usuwanie XF z {changed_count} plików MIDI.", "{changed_count} 個の MIDI ファイルの XF 削除を保留中の変更に追加しました。", "MIDI 파일 {changed_count}개의 XF 제거를 대기열에 추가했습니다.", "已将 {changed_count} 个 MIDI 文件的 XF 移除加入待处理队列。",
    ),
    "Queued pedal compatibility changes for {changed_count} MIDI file(s).": (
        "Se prepararon cambios de compatibilidad de pedales para {changed_count} archivos MIDI.", "Modifications de compatibilité des pédales mises en attente pour {changed_count} fichier(s) MIDI.", "Änderungen der Pedalkompatibilität für {changed_count} MIDI-Datei(en) vorgemerkt.", "Modifiche di compatibilità dei pedali in coda per {changed_count} file MIDI.", "Alterações de compatibilidade dos pedais na fila para {changed_count} arquivo(s) MIDI.", "Промени за съвместимост на педалите са поставени на опашка за {changed_count} MIDI файла.", "Pedaalcompatibiliteitswijzigingen klaargezet voor {changed_count} MIDI-bestand(en).", "Dodano do kolejki zmiany zgodności pedałów dla {changed_count} plików MIDI.", "{changed_count} 個の MIDI ファイルのペダル互換性変更を保留中の変更に追加しました。", "MIDI 파일 {changed_count}개의 페달 호환성 변경을 대기열에 추가했습니다.", "已将 {changed_count} 个 MIDI 文件的踏板兼容性更改加入待处理队列。",
    ),
    "Queued {count} file(s) for {source_kind} -> {target_kind} conversion.": (
        "Se prepararon {count} archivos para la conversión de {source_kind} a {target_kind}.", "{count} fichier(s) mis en attente pour la conversion {source_kind} → {target_kind}.", "{count} Datei(en) für die Konvertierung {source_kind} → {target_kind} vorgemerkt.", "{count} file in coda per la conversione {source_kind} → {target_kind}.", "{count} arquivo(s) na fila para conversão de {source_kind} para {target_kind}.", "Файлове на опашка за преобразуване {source_kind} → {target_kind}: {count}.", "{count} bestand(en) klaargezet voor conversie van {source_kind} naar {target_kind}.", "Dodano {count} plików do kolejki konwersji {source_kind} → {target_kind}.", "{count} 個のファイルの {source_kind} → {target_kind} 変換を保留中の変更に追加しました。", "파일 {count}개의 {source_kind} → {target_kind} 변환을 대기열에 추가했습니다.", "已将 {count} 个文件的 {source_kind} → {target_kind} 转换加入待处理队列。",
    ),
    "Queued {count} file(s) to add to the image.": (
        "Se preparó la adición de {count} archivos a la imagen.", "{count} fichier(s) en attente d'ajout à l'image.", "{count} Datei(en) zum Hinzufügen zum Image vorgemerkt.", "{count} file in coda per l'aggiunta all'immagine.", "{count} arquivo(s) na fila para adicionar à imagem.", "Файлове на опашка за добавяне към образа: {count}.", "{count} bestand(en) klaargezet om aan de image toe te voegen.", "Dodano {count} plików do kolejki dodawania do obrazu.", "イメージに追加する {count} 個のファイルを保留中の変更に追加しました。", "이미지에 추가할 파일 {count}개를 대기열에 추가했습니다.", "已将 {count} 个文件加入待添加到映像的队列。",
    ),
    "Queued {count} file(s) to replace matching filenames.": (
        "Se preparó la sustitución de {count} archivos con nombres coincidentes.", "{count} fichier(s) en attente de remplacement des fichiers de même nom.", "{count} Datei(en) zum Ersetzen gleichnamiger Dateien vorgemerkt.", "{count} file in coda per sostituire i file con lo stesso nome.", "{count} arquivo(s) na fila para substituir arquivos com nomes iguais.", "Файлове на опашка за замяна на файлове със същите имена: {count}.", "{count} bestand(en) klaargezet om gelijknamige bestanden te vervangen.", "Dodano {count} plików do kolejki zastępowania plików o tych samych nazwach.", "同名のファイルを置き換える {count} 個のファイルを保留中の変更に追加しました。", "동일한 이름의 파일을 교체하도록 파일 {count}개를 대기열에 추가했습니다.", "已将 {count} 个文件加入待替换同名文件的队列。",
    ),
    "Queued {staged_internal_count} internal filename change(s). Use Save or Save As Image to write them.": (
        "Se prepararon {staged_internal_count} cambios de nombres internos. Usa Guardar o Guardar como imagen para aplicarlos.", "{staged_internal_count} changement(s) de nom interne en attente. Utilisez Enregistrer ou Enregistrer comme image pour les écrire.", "{staged_internal_count} interne Dateinamensänderung(en) vorgemerkt. Verwenden Sie Speichern oder Als Image speichern, um sie zu schreiben.", "{staged_internal_count} modifiche ai nomi interni in coda. Usa Salva o Salva come immagine per scriverle.", "{staged_internal_count} alteração(ões) de nomes internos na fila. Use Salvar ou Salvar como imagem para gravá-las.", "Промени на вътрешни имена на опашка: {staged_internal_count}. Използвайте Запис или Запис като образ, за да ги запишете.", "{staged_internal_count} interne bestandsnaamwijziging(en) klaargezet. Gebruik Opslaan of Opslaan als image om ze te schrijven.", "Dodano do kolejki {staged_internal_count} zmian nazw wewnętrznych. Użyj Zapisz lub Zapisz jako obraz, aby je zapisać.", "{staged_internal_count} 件の内部ファイル名変更を保留中の変更に追加しました。「保存」または「イメージとして保存」で書き込みます。", "내부 파일 이름 변경 {staged_internal_count}건을 대기열에 추가했습니다. 저장 또는 이미지로 저장을 사용하여 기록하세요.", "已将 {staged_internal_count} 项内部文件名更改加入待处理队列。请使用“保存”或“另存为映像”写入。",
    ),
    "Read the floppy and queued {count} file(s) for E-SEQ -> MIDI conversion.": (
        "Se leyó el disquete y se prepararon {count} archivos para convertir de E-SEQ a MIDI.", "Disquette lue ; {count} fichier(s) en attente de conversion E-SEQ → MIDI.", "Diskette gelesen und {count} Datei(en) für die Konvertierung E-SEQ → MIDI vorgemerkt.", "Dischetto letto e {count} file in coda per la conversione E-SEQ → MIDI.", "Disquete lido e {count} arquivo(s) na fila para conversão de E-SEQ para MIDI.", "Дискетата е прочетена; файлове на опашка за преобразуване E-SEQ → MIDI: {count}.", "Diskette gelezen en {count} bestand(en) klaargezet voor conversie van E-SEQ naar MIDI.", "Odczytano dyskietkę i dodano {count} plików do kolejki konwersji E-SEQ → MIDI.", "フロッピーを読み取り、{count} 個のファイルの E-SEQ → MIDI 変換を保留中の変更に追加しました。", "플로피를 읽고 파일 {count}개의 E-SEQ → MIDI 변환을 대기열에 추가했습니다.", "已读取软盘，并将 {count} 个文件的 E-SEQ → MIDI 转换加入待处理队列。",
    ),
    "Removed extra spacing from {trimmed_title_count} song title(s).": (
        "Se eliminaron los espacios sobrantes de {trimmed_title_count} títulos.", "Espaces superflus supprimés dans {trimmed_title_count} titre(s).", "Überflüssige Leerzeichen aus {trimmed_title_count} Songtitel(n) entfernt.", "Rimossi gli spazi in eccesso da {trimmed_title_count} titoli.", "Espaços extras removidos de {trimmed_title_count} título(s).", "Премахнати са излишните интервали от {trimmed_title_count} заглавия.", "Overbodige spaties uit {trimmed_title_count} songtitel(s) verwijderd.", "Usunięto nadmiarowe spacje z {trimmed_title_count} tytułów utworów.", "{trimmed_title_count} 件の曲名から余分な空白を削除しました。", "곡 제목 {trimmed_title_count}개에서 불필요한 공백을 제거했습니다.", "已移除 {trimmed_title_count} 个乐曲标题中的多余空格。",
    ),
    "Requested metadata cleanup was staged; the change review shows removed records.": (
        "Se preparó la limpieza de metadatos solicitada; la revisión de cambios muestra los registros eliminados.", "Le nettoyage des métadonnées demandé est préparé ; la révision des modifications montre les enregistrements supprimés.", "Die angeforderte Metadatenbereinigung ist vorgemerkt; die Änderungsübersicht zeigt die entfernten Einträge.", "La pulizia dei metadati richiesta è stata preparata; la revisione delle modifiche mostra i record rimossi.", "A limpeza de metadados solicitada foi preparada; a revisão das alterações mostra os registros removidos.", "Заявеното почистване на метаданните е подготвено; прегледът на промените показва премахнатите записи.", "De gevraagde opschoning van metadata is klaargezet; het wijzigingsoverzicht toont de verwijderde records.", "Przygotowano żądane czyszczenie metadanych; przegląd zmian pokazuje usunięte rekordy.", "指定されたメタデータ削除を準備しました。変更の確認で削除されるレコードを確認できます。", "요청한 메타데이터 정리를 준비했습니다. 변경 검토에서 제거된 레코드를 확인할 수 있습니다.", "已暂存所请求的元数据清理；更改审查中显示已移除的记录。",
    ),
    "Shortened {count} E-SEQ filename(s) to DOS 8.3.": (
        "Se acortaron {count} nombres de archivo E-SEQ al formato DOS 8.3.", "{count} nom(s) de fichier E-SEQ raccourci(s) au format DOS 8.3.", "{count} E-SEQ-Dateiname(n) auf DOS 8.3 gekürzt.", "Accorciati {count} nomi di file E-SEQ al formato DOS 8.3.", "{count} nome(s) de arquivo E-SEQ encurtado(s) para DOS 8.3.", "Съкратени E-SEQ имена на файлове до DOS 8.3: {count}.", "{count} E-SEQ-bestandsnaam/namen ingekort tot DOS 8.3.", "Skrócono {count} nazw plików E-SEQ do formatu DOS 8.3.", "{count} 個の E-SEQ ファイル名を DOS 8.3 形式に短縮しました。", "E-SEQ 파일 이름 {count}개를 DOS 8.3 형식으로 줄였습니다.", "已将 {count} 个 E-SEQ 文件名缩短为 DOS 8.3 格式。",
    ),
    "Skipped {count} file(s).": (
        "Se omitieron {count} archivos.", "{count} fichier(s) ignoré(s).", "{count} Datei(en) übersprungen.", "Ignorati {count} file.", "{count} arquivo(s) ignorado(s).", "Пропуснати файлове: {count}.", "{count} bestand(en) overgeslagen.", "Pominięto plików: {count}.", "{count} 個のファイルをスキップしました。", "파일 {count}개를 건너뛰었습니다.", "已跳过 {count} 个文件。",
    ),
})
_ROWS.update({
    "Staged {converted_count} dropped file(s) for automatic conversion.": (
        "Se prepararon {converted_count} archivos arrastrados para la conversión automática.", "{converted_count} fichier(s) déposé(s) préparé(s) pour la conversion automatique.", "{converted_count} abgelegte Datei(en) für die automatische Konvertierung vorgemerkt.", "Preparati {converted_count} file trascinati per la conversione automatica.", "{converted_count} arquivo(s) arrastado(s) preparado(s) para conversão automática.", "Подготвени за автоматично преобразуване файлове, добавени чрез плъзгане: {converted_count}.", "{converted_count} gesleepte bestand(en) klaargezet voor automatische conversie.", "Przygotowano {converted_count} upuszczonych plików do automatycznej konwersji.", "ドロップした {converted_count} 個のファイルの自動変換を準備しました。", "놓은 파일 {converted_count}개의 자동 변환을 준비했습니다.", "已暂存 {converted_count} 个拖放文件的自动转换。",
    ),
    "Staged {converted_count} file(s) for MIDI Type 0 conversion.": (
        "Se prepararon {converted_count} archivos para la conversión a MIDI tipo 0.", "{converted_count} fichier(s) préparé(s) pour la conversion en MIDI de type 0.", "{converted_count} Datei(en) für die Konvertierung in MIDI Typ 0 vorgemerkt.", "Preparati {converted_count} file per la conversione in MIDI tipo 0.", "{converted_count} arquivo(s) preparado(s) para conversão em MIDI tipo 0.", "Подготвени файлове за преобразуване в MIDI тип 0: {converted_count}.", "{converted_count} bestand(en) klaargezet voor conversie naar MIDI type 0.", "Przygotowano {converted_count} plików do konwersji na MIDI typu 0.", "{converted_count} 個のファイルの MIDI タイプ 0 への変換を準備しました。", "파일 {converted_count}개의 MIDI 유형 0 변환을 준비했습니다.", "已暂存 {converted_count} 个文件的 MIDI 类型 0 转换。",
    ),
    "Staged {converted_count} file(s) for {source_kind} -> {target_kind} conversion.\nUse Save, Save As, or Save As Image to write the converted files.": (
        "Se prepararon {converted_count} archivos para la conversión de {source_kind} a {target_kind}.\nUsa Guardar, Guardar como o Guardar como imagen para escribir los archivos convertidos.",
        "{converted_count} fichier(s) préparé(s) pour la conversion {source_kind} → {target_kind}.\nUtilisez Enregistrer, Enregistrer sous ou Enregistrer comme image pour écrire les fichiers convertis.",
        "{converted_count} Datei(en) für die Konvertierung {source_kind} → {target_kind} vorgemerkt.\nVerwenden Sie Speichern, Speichern unter oder Als Image speichern, um die konvertierten Dateien zu schreiben.",
        "Preparati {converted_count} file per la conversione {source_kind} → {target_kind}.\nUsa Salva, Salva con nome o Salva come immagine per scrivere i file convertiti.",
        "{converted_count} arquivo(s) preparado(s) para conversão de {source_kind} para {target_kind}.\nUse Salvar, Salvar como ou Salvar como imagem para gravar os arquivos convertidos.",
        "Подготвени файлове за преобразуване {source_kind} → {target_kind}: {converted_count}.\nИзползвайте Запис, Запис като или Запис като образ, за да запишете преобразуваните файлове.",
        "{converted_count} bestand(en) klaargezet voor conversie van {source_kind} naar {target_kind}.\nGebruik Opslaan, Opslaan als of Opslaan als image om de geconverteerde bestanden te schrijven.",
        "Przygotowano {converted_count} plików do konwersji {source_kind} → {target_kind}.\nUżyj Zapisz, Zapisz jako lub Zapisz jako obraz, aby zapisać przekonwertowane pliki.",
        "{converted_count} 個のファイルの {source_kind} → {target_kind} 変換を準備しました。\n「保存」「別名で保存」「イメージとして保存」で変換済みファイルを書き込みます。",
        "파일 {converted_count}개의 {source_kind} → {target_kind} 변환을 준비했습니다.\n저장, 다른 이름으로 저장 또는 이미지로 저장을 사용하여 변환한 파일을 기록하세요.",
        "已暂存 {converted_count} 个文件的 {source_kind} → {target_kind} 转换。\n请使用“保存”、“另存为”或“另存为映像”写入转换后的文件。",
    ),
    "Staged {staged_count} DOS 8.3 filename change(s).": (
        "Se prepararon {staged_count} cambios de nombres DOS 8.3.", "{staged_count} changement(s) de nom DOS 8.3 préparé(s).", "{staged_count} DOS-8.3-Dateinamensänderung(en) vorgemerkt.", "Preparate {staged_count} modifiche ai nomi DOS 8.3.", "{staged_count} alteração(ões) de nomes DOS 8.3 preparada(s).", "Подготвени промени на имена DOS 8.3: {staged_count}.", "{staged_count} DOS 8.3-bestandsnaamwijziging(en) klaargezet.", "Przygotowano {staged_count} zmian nazw plików DOS 8.3.", "{staged_count} 件の DOS 8.3 ファイル名変更を準備しました。", "DOS 8.3 파일 이름 변경 {staged_count}건을 준비했습니다.", "已暂存 {staged_count} 项 DOS 8.3 文件名更改。",
    ),
    "The Filename column shows the Save As names; internal names remain DOS 8.3.": (
        "La columna Archivo muestra los nombres de Guardar como; los nombres internos siguen en DOS 8.3.", "La colonne Nom de fichier affiche les noms pour Enregistrer sous ; les noms internes restent au format DOS 8.3.", "Die Spalte Dateiname zeigt die Namen für Speichern unter; interne Namen bleiben im DOS-8.3-Format.", "La colonna Nome file mostra i nomi per Salva con nome; i nomi interni restano in formato DOS 8.3.", "A coluna Arquivo mostra os nomes de Salvar como; os nomes internos permanecem em DOS 8.3.", "Колоната Име на файл показва имената за Запис като; вътрешните имена остават във формат DOS 8.3.", "De kolom Bestandsnaam toont de namen voor Opslaan als; interne namen blijven DOS 8.3.", "Kolumna Nazwa pliku pokazuje nazwy dla Zapisz jako; nazwy wewnętrzne pozostają w formacie DOS 8.3.", "ファイル名列には「別名で保存」の名前を表示します。内部の名前は DOS 8.3 のままです。", "파일 이름 열에는 다른 이름으로 저장할 이름이 표시되며 내부 이름은 DOS 8.3 형식으로 유지됩니다.", "文件名列显示“另存为”使用的名称；内部名称仍为 DOS 8.3 格式。",
    ),
    "Use Save or Save As Image to write the queued names.": (
        "Usa Guardar o Guardar como imagen para escribir los nombres pendientes.", "Utilisez Enregistrer ou Enregistrer comme image pour écrire les noms en attente.", "Verwenden Sie Speichern oder Als Image speichern, um die vorgemerkten Namen zu schreiben.", "Usa Salva o Salva come immagine per scrivere i nomi in coda.", "Use Salvar ou Salvar como imagem para gravar os nomes pendentes.", "Използвайте Запис или Запис като образ, за да запишете чакащите имена.", "Gebruik Opslaan of Opslaan als image om de klaargezette namen te schrijven.", "Użyj Zapisz lub Zapisz jako obraz, aby zapisać oczekujące nazwy.", "「保存」または「イメージとして保存」で保留中の名前を書き込みます。", "저장 또는 이미지로 저장을 사용하여 대기 중인 이름을 기록하세요.", "请使用“保存”或“另存为映像”写入待保存的名称。",
    ),
    "Use Save to overwrite the originals, or Save As to write copies.": (
        "Usa Guardar para sobrescribir los originales o Guardar como para crear copias.", "Utilisez Enregistrer pour écraser les originaux, ou Enregistrer sous pour créer des copies.", "Verwenden Sie Speichern, um die Originale zu überschreiben, oder Speichern unter, um Kopien zu erstellen.", "Usa Salva per sovrascrivere gli originali o Salva con nome per creare copie.", "Use Salvar para sobrescrever os originais ou Salvar como para criar cópias.", "Използвайте Запис, за да презапишете оригиналите, или Запис като, за да създадете копия.", "Gebruik Opslaan om de originelen te overschrijven of Opslaan als om kopieën te maken.", "Użyj Zapisz, aby nadpisać oryginały, lub Zapisz jako, aby utworzyć kopie.", "「保存」で元のファイルを上書きするか、「別名で保存」でコピーを作成します。", "저장으로 원본을 덮어쓰거나 다른 이름으로 저장으로 복사본을 만드세요.", "请使用“保存”覆盖原文件，或使用“另存为”创建副本。",
    ),
    "Use Save to rename originals, or Save As to write renamed copies elsewhere.": (
        "Usa Guardar para renombrar los originales o Guardar como para crear copias con los nuevos nombres en otra ubicación.", "Utilisez Enregistrer pour renommer les originaux, ou Enregistrer sous pour créer des copies renommées ailleurs.", "Verwenden Sie Speichern, um die Originale umzubenennen, oder Speichern unter, um umbenannte Kopien an einem anderen Ort zu erstellen.", "Usa Salva per rinominare gli originali o Salva con nome per creare copie rinominate altrove.", "Use Salvar para renomear os originais ou Salvar como para criar cópias renomeadas em outro local.", "Използвайте Запис, за да преименувате оригиналите, или Запис като, за да запишете преименувани копия на друго място.", "Gebruik Opslaan om de originelen te hernoemen of Opslaan als om elders hernoemde kopieën te maken.", "Użyj Zapisz, aby zmienić nazwy oryginałów, lub Zapisz jako, aby utworzyć kopie o nowych nazwach w innym miejscu.", "「保存」で元のファイル名を変更するか、「別名で保存」で名前を変更したコピーを別の場所に作成します。", "저장으로 원본 이름을 변경하거나 다른 이름으로 저장으로 이름을 바꾼 복사본을 다른 위치에 만드세요.", "请使用“保存”重命名原文件，或使用“另存为”在其他位置创建重命名后的副本。",
    ),
    "Use Save, Save As, or Save As Image to write the converted files.": (
        "Usa Guardar, Guardar como o Guardar como imagen para escribir los archivos convertidos.", "Utilisez Enregistrer, Enregistrer sous ou Enregistrer comme image pour écrire les fichiers convertis.", "Verwenden Sie Speichern, Speichern unter oder Als Image speichern, um die konvertierten Dateien zu schreiben.", "Usa Salva, Salva con nome o Salva come immagine per scrivere i file convertiti.", "Use Salvar, Salvar como ou Salvar como imagem para gravar os arquivos convertidos.", "Използвайте Запис, Запис като или Запис като образ, за да запишете преобразуваните файлове.", "Gebruik Opslaan, Opslaan als of Opslaan als image om de geconverteerde bestanden te schrijven.", "Użyj Zapisz, Zapisz jako lub Zapisz jako obraz, aby zapisać przekonwertowane pliki.", "「保存」「別名で保存」「イメージとして保存」で変換済みファイルを書き込みます。", "저장, 다른 이름으로 저장 또는 이미지로 저장을 사용하여 변환한 파일을 기록하세요.", "请使用“保存”、“另存为”或“另存为映像”写入转换后的文件。",
    ),
    "{count} file(s) could not be added.": (
        "No se pudieron añadir {count} archivos.", "Impossible d'ajouter {count} fichier(s).", "{count} Datei(en) konnten nicht hinzugefügt werden.", "Impossibile aggiungere {count} file.", "Não foi possível adicionar {count} arquivo(s).", "Неуспешно добавяне на {count} файла.", "{count} bestand(en) konden niet worden toegevoegd.", "Nie udało się dodać {count} plików.", "{count} 個のファイルを追加できませんでした。", "파일 {count}개를 추가할 수 없었습니다.", "无法添加 {count} 个文件。",
    ),
    "{count} file(s) could not be converted.": (
        "No se pudieron convertir {count} archivos.", "Impossible de convertir {count} fichier(s).", "{count} Datei(en) konnten nicht konvertiert werden.", "Impossibile convertire {count} file.", "Não foi possível converter {count} arquivo(s).", "Неуспешно преобразуване на {count} файла.", "{count} bestand(en) konden niet worden geconverteerd.", "Nie udało się przekonwertować {count} plików.", "{count} 個のファイルを変換できませんでした。", "파일 {count}개를 변환할 수 없었습니다.", "无法转换 {count} 个文件。",
    ),
    "{count} file(s) failed.": (
        "Falló el procesamiento de {count} archivos.", "Échec pour {count} fichier(s).", "Verarbeitung von {count} Datei(en) fehlgeschlagen.", "Operazione non riuscita per {count} file.", "Falha em {count} arquivo(s).", "Неуспешна обработка на {count} файла.", "Verwerken van {count} bestand(en) mislukt.", "Przetwarzanie {count} plików nie powiodło się.", "{count} 個のファイルの処理に失敗しました。", "파일 {count}개 처리에 실패했습니다.", "{count} 个文件处理失败。",
    ),
    "{unchanged_count} MIDI file(s) did not need changes.": (
        "{unchanged_count} archivos MIDI no necesitaban cambios.", "{unchanged_count} fichier(s) MIDI ne nécessitaient aucune modification.", "{unchanged_count} MIDI-Datei(en) benötigten keine Änderungen.", "{unchanged_count} file MIDI non richiedevano modifiche.", "{unchanged_count} arquivo(s) MIDI não precisavam de alterações.", "MIDI файлове без нужда от промени: {unchanged_count}.", "{unchanged_count} MIDI-bestand(en) hadden geen wijzigingen nodig.", "{unchanged_count} plików MIDI nie wymagało zmian.", "{unchanged_count} 個の MIDI ファイルは変更の必要がありませんでした。", "MIDI 파일 {unchanged_count}개는 변경이 필요하지 않았습니다.", "{unchanged_count} 个 MIDI 文件无需更改。",
    ),
    "{unchanged_count} MIDI file(s) had no eligible binary CC64 stream; continuous or static pedal data was preserved.": (
        "{unchanged_count} archivos MIDI no tenían datos binarios CC64 aptos; se conservaron los datos continuos o estáticos del pedal.", "{unchanged_count} fichier(s) MIDI ne contenaient pas de données binaires CC64 admissibles ; les données continues ou statiques des pédales ont été conservées.", "{unchanged_count} MIDI-Datei(en) enthielten keine geeigneten binären CC64-Daten; kontinuierliche oder statische Pedaldaten blieben erhalten.", "{unchanged_count} file MIDI non contenevano dati binari CC64 idonei; i dati continui o statici dei pedali sono stati conservati.", "{unchanged_count} arquivo(s) MIDI não tinham dados binários CC64 elegíveis; os dados contínuos ou estáticos dos pedais foram preservados.", "MIDI файлове без подходящи двоични данни CC64: {unchanged_count}; непрекъснатите или статичните данни за педалите са запазени.", "{unchanged_count} MIDI-bestand(en) bevatten geen geschikte binaire CC64-gegevens; continue of statische pedaalgegevens zijn behouden.", "{unchanged_count} plików MIDI nie zawierało odpowiednich binarnych danych CC64; zachowano ciągłe lub statyczne dane pedału.", "{unchanged_count} 個の MIDI ファイルには対象となる二値 CC64 データがありませんでした。連続または固定のペダルデータは保持しています。", "MIDI 파일 {unchanged_count}개에 적합한 이진 CC64 데이터가 없어 연속 또는 고정 페달 데이터를 보존했습니다.", "{unchanged_count} 个 MIDI 文件没有符合条件的二值 CC64 数据；保留了连续或静态踏板数据。",
    ),
    "{unchanged_count} already Type 0 and were left unchanged.": (
        "{unchanged_count} ya eran de tipo 0 y no se modificaron.", "{unchanged_count} étaient déjà de type 0 et ont été laissés inchangés.", "{unchanged_count} waren bereits Typ 0 und blieben unverändert.", "{unchanged_count} erano già di tipo 0 e sono rimasti invariati.", "{unchanged_count} já eram tipo 0 e permaneceram inalterados.", "Вече са тип 0 и са оставени непроменени: {unchanged_count}.", "{unchanged_count} waren al type 0 en zijn ongewijzigd gebleven.", "{unchanged_count} było już typu 0 i pozostawiono je bez zmian.", "{unchanged_count} 個はすでにタイプ 0 のため変更していません。", "{unchanged_count}개는 이미 유형 0이어서 변경하지 않았습니다.", "{unchanged_count} 个文件已为类型 0，保持不变。",
    ),
    "{unchanged_count} already matched and were left unchanged.": (
        "{unchanged_count} ya coincidían y no se modificaron.", "{unchanged_count} correspondaient déjà et ont été laissés inchangés.", "{unchanged_count} stimmten bereits überein und blieben unverändert.", "{unchanged_count} corrispondevano già e sono rimasti invariati.", "{unchanged_count} já correspondiam e permaneceram inalterados.", "Вече съвпадат и са оставени непроменени: {unchanged_count}.", "{unchanged_count} kwamen al overeen en zijn ongewijzigd gebleven.", "{unchanged_count} już pasowało i pozostawiono je bez zmian.", "{unchanged_count} 個はすでに一致していたため変更していません。", "{unchanged_count}개는 이미 일치하여 변경하지 않았습니다.", "{unchanged_count} 个文件已匹配，保持不变。",
    ),
})

STATUS_TRANSLATIONS = {
    source: dict(zip(_LANGUAGES, translations, strict=True))
    for source, translations in _ROWS.items()
}

# These source variants describe the same pending operation in different views.
for _source, _equivalent in (
    ("Staged XF removal for {changed_count} MIDI file(s).", "Queued XF removal for {changed_count} MIDI file(s)."),
    ("Staged pedal compatibility changes for {changed_count} MIDI file(s).", "Queued pedal compatibility changes for {changed_count} MIDI file(s)."),
    ("{count} file(s) failed conversion.", "{count} file(s) could not be converted."),
):
    STATUS_TRANSLATIONS[_source] = dict(STATUS_TRANSLATIONS[_equivalent])
STATUS_TRANSLATIONS["Skipped {skipped_count} file(s)."] = {
    language: text.replace("{count}", "{skipped_count}")
    for language, text in STATUS_TRANSLATIONS["Skipped {count} file(s)."].items()
}
_cleanup = "Requested metadata cleanup was staged; the change review shows removed records."
_save = "Use Save to overwrite the originals, or Save As to write copies."
STATUS_TRANSLATIONS[_cleanup + " " + _save] = {
    language: STATUS_TRANSLATIONS[_cleanup][language] + " " + STATUS_TRANSLATIONS[_save][language]
    for language in _LANGUAGES
}
