"""Labels specific to the main window's menus.

Keep these separate from existing dialog and toolbar labels so making menus
clearer does not change wording elsewhere in the application.
"""


def _translations(*values):
    languages = ("es", "fr", "de", "it", "pt-BR", "bg", "nl", "pl", "ja", "ko", "zh-Hans")
    if len(values) != len(languages):
        raise ValueError("Window menu translations must cover every language.")
    return dict(zip(languages, values))


WINDOW_MENU_TRANSLATIONS = {
    "Quit": _translations(
        "Salir", "Quitter", "Beenden", "Esci", "Sair", "Изход",
        "Afsluiten", "Zakończ", "終了", "종료", "退出",
    ),
    "Create Image from Floppy...": _translations(
        "Crear imagen de disquete...", "Créer une image de disquette...",
        "Abbild von Diskette erstellen...", "Crea immagine da floppy...",
        "Criar imagem de disquete...", "Създаване на образ от дискета...",
        "Image van diskette maken...", "Utwórz obraz dyskietki...",
        "フロッピーからイメージを作成...", "플로피에서 이미지 생성...", "从软盘创建映像...",
    ),
    "Save Files to Floppy...": _translations(
        "Guardar archivos en disquete...", "Enregistrer les fichiers sur disquette...",
        "Dateien auf Diskette speichern...", "Salva file su floppy...",
        "Salvar arquivos em disquete...", "Запис на файлове на дискета...",
        "Bestanden op diskette opslaan...", "Zapisz pliki na dyskietce...",
        "ファイルをフロッピーに保存...", "플로피에 파일 저장...", "将文件保存到软盘...",
    ),
    "Show Preparation Controls": _translations(
        "Mostrar controles de preparación", "Afficher les commandes de préparation",
        "Vorbereitungssteuerung anzeigen", "Mostra controlli di preparazione",
        "Mostrar controles de preparação", "Показване на контролите за подготовка",
        "Voorbereidingsbediening tonen", "Pokaż elementy sterowania przygotowaniem",
        "準備用コントロールを表示", "준비 컨트롤 표시", "显示准备控件",
    ),
    "Back Up Before Saving": _translations(
        "Crear copia antes de guardar", "Sauvegarder avant d'enregistrer",
        "Vor dem Speichern sichern", "Backup prima del salvataggio",
        "Fazer backup antes de salvar", "Резервно копие преди запис",
        "Back-up maken vóór opslaan", "Utwórz kopię przed zapisem",
        "保存前にバックアップ", "저장 전 백업", "保存前备份",
    ),
    "Show Dismissed Messages Again": _translations(
        "Volver a mostrar los mensajes ocultos", "Réafficher les messages masqués",
        "Ausgeblendete Meldungen erneut anzeigen", "Mostra di nuovo i messaggi nascosti",
        "Mostrar novamente as mensagens ocultadas", "Повторно показване на скритите съобщения",
        "Verborgen meldingen opnieuw tonen", "Pokaż ponownie ukryte komunikaty",
        "非表示にしたメッセージを再表示", "숨긴 메시지 다시 표시", "重新显示已隐藏的消息",
    ),
    "Disk Options": _translations(
        "Opciones de disco", "Options de disque", "Diskettenoptionen",
        "Opzioni disco", "Opções de disco", "Настройки за дискове",
        "Schijfopties", "Opcje dysku", "ディスク設定", "디스크 옵션", "磁盘选项",
    ),
    "Create Album Subfolder for Folder Exports": _translations(
        "Crear subcarpeta de álbum al exportar a carpetas",
        "Créer un sous-dossier d’album pour les exports vers un dossier",
        "Album-Unterordner beim Export in Ordner erstellen",
        "Crea sottocartella dell’album per le esportazioni in cartelle",
        "Criar subpasta do álbum ao exportar para pastas",
        "Създаване на подпапка за албума при експортиране в папка",
        "Albumsubmap maken bij exporteren naar mappen",
        "Utwórz podfolder albumu przy eksporcie do folderów",
        "フォルダーへの書き出し時にアルバムのサブフォルダーを作成",
        "폴더로 내보낼 때 앨범 하위 폴더 생성", "导出到文件夹时创建专辑子文件夹",
    ),
    "Use DOS 8.3 Filenames": _translations(
        "Usar nombres de archivo DOS 8.3", "Utiliser des noms de fichiers DOS 8.3",
        "DOS-8.3-Dateinamen verwenden", "Usa nomi file DOS 8.3",
        "Usar nomes de arquivo DOS 8.3", "Използване на имена на файлове DOS 8.3",
        "DOS 8.3-bestandsnamen gebruiken", "Używaj nazw plików DOS 8.3",
        "DOS 8.3形式のファイル名を使用", "DOS 8.3 파일 이름 사용", "使用 DOS 8.3 文件名",
    ),
    "Save Partial Capture...": _translations(
        "Guardar captura parcial...", "Enregistrer la capture partielle...",
        "Teilaufnahme speichern...", "Salva acquisizione parziale...",
        "Salvar captura parcial...", "Запис на частичния образ...",
        "Gedeeltelijke opname opslaan...", "Zapisz częściowy obraz...",
        "部分キャプチャを保存...", "부분 캡처 저장...", "保存部分捕获...",
    ),
    "Verify Floppy Contents After Writing": _translations(
        "Verificar el contenido del disquete después de escribir",
        "Vérifier le contenu de la disquette après l’écriture",
        "Disketteninhalt nach dem Schreiben prüfen",
        "Verifica il contenuto del floppy dopo la scrittura",
        "Verificar conteúdo do disquete após gravar",
        "Проверка на съдържанието на дискетата след запис",
        "Diskette-inhoud na schrijven controleren",
        "Sprawdź zawartość dyskietki po zapisie",
        "書き込み後にフロッピーの内容を検証", "쓰기 후 플로피 내용 검증", "写入后验证软盘内容",
    ),
    "MIDI Type 1 to Type 0": _translations(
        "MIDI de tipo 1 a tipo 0", "MIDI type 1 vers type 0",
        "MIDI-Typ 1 in Typ 0", "MIDI da tipo 1 a tipo 0",
        "MIDI tipo 1 para tipo 0", "MIDI от тип 1 към тип 0",
        "MIDI-type 1 naar type 0", "MIDI typu 1 na typ 0",
        "MIDIタイプ1からタイプ0へ", "MIDI 유형 1을 유형 0으로", "MIDI 类型 1 转为类型 0",
    ),
    "Review Changes...": _translations(
        "Revisar cambios...", "Examiner les modifications...", "Änderungen prüfen...",
        "Esamina modifiche...", "Revisar alterações...", "Преглед на промените...",
        "Wijzigingen bekijken...", "Przejrzyj zmiany...",
        "変更を確認...", "변경 사항 검토...", "查看更改...",
    ),
}
