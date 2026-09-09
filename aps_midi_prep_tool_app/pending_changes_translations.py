"""Labels for reviewing and reversing staged work."""

_ROWS = {
    "en": ("{count} songs have unsaved changes", "No unsaved song changes", "Review Changes", "Undo Last Batch", "Discard Selected Song Changes", "Original", "Proposed", "Changes", "Save destination: {destination}", "Save unavailable: {reason}", "The last batch of staged changes was undone.", "Catalog or album changes are pending."),
    "es": ("{count} canciones tienen cambios sin guardar", "No hay cambios de canciones sin guardar", "Revisar cambios", "Deshacer el último lote", "Descartar cambios de las canciones seleccionadas", "Original", "Propuesto", "Cambios", "Destino de guardado: {destination}", "Guardar no disponible: {reason}", "Se deshizo el último lote de cambios pendientes.", "Hay cambios pendientes en el catálogo o álbum."),
    "fr": ("{count} morceaux ont des modifications non enregistrées", "Aucune modification de morceau non enregistrée", "Examiner les modifications", "Annuler le dernier lot", "Abandonner les modifications des morceaux sélectionnés", "Original", "Proposé", "Modifications", "Destination d’enregistrement : {destination}", "Enregistrement indisponible : {reason}", "Le dernier lot de modifications en attente a été annulé.", "Des modifications du catalogue ou de l’album sont en attente."),
    "de": ("{count} Stücke haben ungespeicherte Änderungen", "Keine ungespeicherten Änderungen an Stücken", "Änderungen prüfen", "Letzten Stapel rückgängig machen", "Änderungen ausgewählter Stücke verwerfen", "Original", "Vorgeschlagen", "Änderungen", "Speicherziel: {destination}", "Speichern nicht verfügbar: {reason}", "Der letzte Stapel vorgemerkter Änderungen wurde rückgängig gemacht.", "Katalog- oder Albumänderungen stehen aus."),
    "it": ("{count} brani hanno modifiche non salvate", "Nessuna modifica ai brani da salvare", "Esamina modifiche", "Annulla ultimo gruppo", "Scarta modifiche ai brani selezionati", "Originale", "Proposto", "Modifiche", "Destinazione di salvataggio: {destination}", "Salvataggio non disponibile: {reason}", "L’ultimo gruppo di modifiche in attesa è stato annullato.", "Sono in attesa modifiche al catalogo o all’album."),
    "pt-BR": ("{count} músicas têm alterações não salvas", "Nenhuma alteração de música não salva", "Revisar alterações", "Desfazer último lote", "Descartar alterações das músicas selecionadas", "Original", "Proposto", "Alterações", "Destino de salvamento: {destination}", "Salvar indisponível: {reason}", "O último lote de alterações pendentes foi desfeito.", "Há alterações pendentes no catálogo ou álbum."),
    "bg": ("{count} песни имат незаписани промени", "Няма незаписани промени по песните", "Преглед на промените", "Отмяна на последната група", "Отхвърляне на промените по избраните песни", "Оригинал", "Предложено", "Промени", "Място за запис: {destination}", "Записът е недостъпен: {reason}", "Последната група подготвени промени е отменена.", "Има чакащи промени по каталога или албума."),
    "nl": ("{count} nummers hebben niet-opgeslagen wijzigingen", "Geen niet-opgeslagen wijzigingen aan nummers", "Wijzigingen bekijken", "Laatste groep ongedaan maken", "Wijzigingen aan geselecteerde nummers verwerpen", "Origineel", "Voorgesteld", "Wijzigingen", "Opslagbestemming: {destination}", "Opslaan niet beschikbaar: {reason}", "De laatste groep voorbereide wijzigingen is ongedaan gemaakt.", "Er zijn catalogus- of albumwijzigingen in behandeling."),
    "pl": ("{count} utworów ma niezapisane zmiany", "Brak niezapisanych zmian w utworach", "Przejrzyj zmiany", "Cofnij ostatnią partię", "Odrzuć zmiany wybranych utworów", "Oryginał", "Propozycja", "Zmiany", "Miejsce zapisu: {destination}", "Zapis niedostępny: {reason}", "Cofnięto ostatnią partię przygotowanych zmian.", "Oczekują zmiany katalogu lub albumu."),
    "ja": ("{count} 曲に未保存の変更があります", "曲の未保存の変更はありません", "変更を確認", "最後の一括変更を元に戻す", "選択した曲の変更を破棄", "元の内容", "変更後", "変更内容", "保存先: {destination}", "保存できません: {reason}", "最後の一括変更を元に戻しました。", "カタログまたはアルバムの変更が保留中です。"),
    "ko": ("{count}곡에 저장되지 않은 변경 사항이 있습니다", "저장되지 않은 곡 변경 사항이 없습니다", "변경 사항 검토", "마지막 일괄 변경 실행 취소", "선택한 곡의 변경 사항 취소", "원본", "변경 후", "변경 사항", "저장 위치: {destination}", "저장할 수 없음: {reason}", "마지막 일괄 변경을 실행 취소했습니다.", "카탈로그 또는 앨범 변경 사항이 대기 중입니다."),
    "zh-Hans": ("{count} 首歌曲有未保存的更改", "没有未保存的歌曲更改", "查看更改", "撤销上次批量更改", "放弃所选歌曲的更改", "原始", "更改后", "更改", "保存位置：{destination}", "无法保存：{reason}", "已撤销上次暂存的批量更改。", "目录或专辑有待保存的更改。"),
}
_KEYS = ("count", "none", "review", "undo", "discard", "original", "proposed", "changes", "destination", "disabled", "undone", "catalog")
PENDING_CHANGE_MESSAGES = {
    "pending." + key: {language: values[index] for language, values in _ROWS.items()}
    for index, key in enumerate(_KEYS)
}
_UNDO_ROWS = {
    "en": ("Undo", "Undo All", "The last change was undone.", "Returned to the state before these changes."),
    "es": ("Deshacer", "Deshacer todo", "Se deshizo el último cambio.", "Se restauró el estado anterior a estos cambios."),
    "fr": ("Annuler", "Tout annuler", "La dernière modification a été annulée.", "L’état antérieur à ces modifications a été rétabli."),
    "de": ("Rückgängig", "Alles rückgängig", "Die letzte Änderung wurde rückgängig gemacht.", "Der Zustand vor diesen Änderungen wurde wiederhergestellt."),
    "it": ("Annulla", "Annulla tutto", "L’ultima modifica è stata annullata.", "È stato ripristinato lo stato precedente a queste modifiche."),
    "pt-BR": ("Desfazer", "Desfazer tudo", "A última alteração foi desfeita.", "O estado anterior a estas alterações foi restaurado."),
    "bg": ("Отмяна", "Отмяна на всичко", "Последната промяна е отменена.", "Възстановено е състоянието преди тези промени."),
    "nl": ("Ongedaan maken", "Alles ongedaan maken", "De laatste wijziging is ongedaan gemaakt.", "De toestand vóór deze wijzigingen is hersteld."),
    "pl": ("Cofnij", "Cofnij wszystko", "Ostatnia zmiana została cofnięta.", "Przywrócono stan sprzed tych zmian."),
    "ja": ("元に戻す", "すべて元に戻す", "最後の変更を元に戻しました。", "これらの変更前の状態に戻しました。"),
    "ko": ("실행 취소", "모두 실행 취소", "마지막 변경을 실행 취소했습니다.", "변경 전 상태로 돌아갔습니다."),
    "zh-Hans": ("撤销", "全部撤销", "已撤销上次更改。", "已恢复到这些更改之前的状态。"),
}
for _index, _key in enumerate(("undo", "undo_all", "undone", "all_undone")):
    PENDING_CHANGE_MESSAGES["pending." + _key] = {
        language: values[_index] for language, values in _UNDO_ROWS.items()
    }
PENDING_CHANGE_MESSAGES["pending.undo_all_failed"] = {
    "en": "Could not undo all changes. Pending edits were kept.\n\n{error}",
    "es": "No se pudieron deshacer todos los cambios. Se conservaron las ediciones pendientes.\n\n{error}",
    "fr": "Impossible d’annuler toutes les modifications. Les modifications en attente ont été conservées.\n\n{error}",
    "de": "Nicht alle Änderungen konnten rückgängig gemacht werden. Ausstehende Änderungen wurden beibehalten.\n\n{error}",
    "it": "Impossibile annullare tutte le modifiche. Le modifiche in attesa sono state conservate.\n\n{error}",
    "pt-BR": "Não foi possível desfazer todas as alterações. As edições pendentes foram mantidas.\n\n{error}",
    "bg": "Не всички промени могат да бъдат отменени. Чакащите редакции са запазени.\n\n{error}",
    "nl": "Niet alle wijzigingen konden ongedaan worden gemaakt. Openstaande wijzigingen zijn behouden.\n\n{error}",
    "pl": "Nie udało się cofnąć wszystkich zmian. Oczekujące zmiany zostały zachowane.\n\n{error}",
    "ja": "すべての変更を元に戻せませんでした。保留中の編集内容は保持されています。\n\n{error}",
    "ko": "모든 변경을 실행 취소할 수 없습니다. 대기 중인 편집 내용은 유지됩니다.\n\n{error}",
    "zh-Hans": "无法撤销所有更改。待处理的编辑已保留。\n\n{error}",
}
PENDING_CHANGE_MESSAGES["pending.order"] = {
    "en": "Playback order: {before} → {after}.",
    "es": "Orden de reproducción: {before} → {after}.",
    "fr": "Ordre de lecture : {before} → {after}.",
    "de": "Wiedergabereihenfolge: {before} → {after}.",
    "it": "Ordine di riproduzione: {before} → {after}.",
    "pt-BR": "Ordem de reprodução: {before} → {after}.",
    "bg": "Ред на възпроизвеждане: {before} → {after}.",
    "nl": "Afspeelvolgorde: {before} → {after}.",
    "pl": "Kolejność odtwarzania: {before} → {after}.",
    "ja": "再生順: {before} → {after}。",
    "ko": "재생 순서: {before} → {after}.",
    "zh-Hans": "播放顺序：{before} → {after}。",
}
PENDING_CHANGE_MESSAGES["pending.folders"] = {
    "en": "{path} ({count} folders)", "es": "{path} ({count} carpetas)",
    "fr": "{path} ({count} dossiers)", "de": "{path} ({count} Ordner)",
    "it": "{path} ({count} cartelle)", "pt-BR": "{path} ({count} pastas)",
    "bg": "{path} ({count} папки)", "nl": "{path} ({count} mappen)",
    "pl": "{path} ({count} folderów)", "ja": "{path}（{count} フォルダー）",
    "ko": "{path} ({count}개 폴더)", "zh-Hans": "{path}（{count} 个文件夹）",
}
PENDING_CHANGE_MESSAGES["save_as.retry"] = {
    "en": "The original list and edits are retained. Review the destination and use Save As again to retry.",
    "es": "Se conservan la lista original y las ediciones. Revise el destino y use Guardar como de nuevo para reintentar.",
    "fr": "La liste et les modifications d’origine sont conservées. Vérifiez la destination et réessayez avec Enregistrer sous.",
    "de": "Die ursprüngliche Liste und die Änderungen bleiben erhalten. Prüfen Sie das Ziel und versuchen Sie erneut Speichern unter.",
    "it": "L’elenco originale e le modifiche sono conservati. Controlla la destinazione e riprova con Salva con nome.",
    "pt-BR": "A lista original e as edições foram mantidas. Revise o destino e use Salvar como novamente para tentar de novo.",
    "bg": "Оригиналният списък и редакциите са запазени. Проверете местоназначението и опитайте отново със Запис като.",
    "nl": "De oorspronkelijke lijst en wijzigingen blijven bewaard. Controleer de bestemming en probeer Opslaan als opnieuw.",
    "pl": "Oryginalna lista i zmiany zostały zachowane. Sprawdź miejsce docelowe i spróbuj ponownie użyć Zapisz jako.",
    "ja": "元のリストと編集内容は保持されています。保存先を確認し、名前を付けて保存で再試行してください。",
    "ko": "원래 목록과 편집 내용이 유지됩니다. 저장 위치를 확인하고 다른 이름으로 저장을 다시 사용하세요.",
    "zh-Hans": "原始列表和编辑已保留。请检查目标位置，然后再次使用另存为重试。",
}
PENDING_CHANGE_MESSAGES["save_as.source_conflict"] = {
    "en": "{filename} would overwrite a listed source. Choose another folder or use Save.",
    "es": "{filename} sobrescribiría un archivo de origen de la lista. Elija otra carpeta o use Guardar.",
    "fr": "{filename} remplacerait un fichier source de la liste. Choisissez un autre dossier ou utilisez Enregistrer.",
    "de": "{filename} würde eine aufgeführte Quelldatei überschreiben. Wählen Sie einen anderen Ordner oder verwenden Sie Speichern.",
    "it": "{filename} sovrascriverebbe un file sorgente nell’elenco. Scegli un’altra cartella o usa Salva.",
    "pt-BR": "{filename} sobrescreveria um arquivo de origem listado. Escolha outra pasta ou use Salvar.",
    "bg": "{filename} ще презапише изходен файл от списъка. Изберете друга папка или използвайте Запис.",
    "nl": "{filename} zou een bronbestand uit de lijst overschrijven. Kies een andere map of gebruik Opslaan.",
    "pl": "{filename} nadpisałby plik źródłowy z listy. Wybierz inny folder lub użyj Zapisz.",
    "ja": "{filename} はリスト内の元ファイルを上書きします。別のフォルダーを選ぶか、保存を使用してください。",
    "ko": "{filename}이 목록의 원본 파일을 덮어씁니다. 다른 폴더를 선택하거나 저장을 사용하세요.",
    "zh-Hans": "{filename} 将覆盖列表中的源文件。请选择其他文件夹或使用保存。",
}
PENDING_CHANGE_MESSAGES["save_as.duplicate_name"] = {
    "en": "Several songs would be exported as {filename}. Give them different filenames first.",
    "es": "Varias canciones se exportarían como {filename}. Asígneles primero nombres de archivo diferentes.",
    "fr": "Plusieurs morceaux seraient exportés sous le nom {filename}. Donnez-leur d’abord des noms de fichier différents.",
    "de": "Mehrere Stücke würden als {filename} exportiert. Geben Sie ihnen zuerst unterschiedliche Dateinamen.",
    "it": "Più brani verrebbero esportati come {filename}. Assegna prima nomi di file diversi.",
    "pt-BR": "Várias músicas seriam exportadas como {filename}. Primeiro atribua nomes de arquivo diferentes.",
    "bg": "Няколко песни ще бъдат изнесени като {filename}. Първо им задайте различни имена на файлове.",
    "nl": "Meerdere nummers zouden als {filename} worden geëxporteerd. Geef ze eerst verschillende bestandsnamen.",
    "pl": "Kilka utworów zostałoby wyeksportowanych jako {filename}. Najpierw nadaj im różne nazwy plików.",
    "ja": "複数の曲が {filename} として書き出されます。先に異なるファイル名を付けてください。",
    "ko": "여러 곡이 {filename} 이름으로 내보내집니다. 먼저 서로 다른 파일 이름을 지정하세요.",
    "zh-Hans": "多首歌曲将导出为 {filename}。请先为它们设置不同的文件名。",
}
PENDING_CHANGE_MESSAGES["save_as.overwrite.title"] = {
    "en": "Overwrite Files?",
    "es": "¿Sobrescribir archivos?",
    "fr": "Remplacer les fichiers ?",
    "de": "Dateien überschreiben?",
    "it": "Sovrascrivere i file?",
    "pt-BR": "Sobrescrever arquivos?",
    "bg": "Презаписване на файловете?",
    "nl": "Bestanden overschrijven?",
    "pl": "Nadpisać pliki?",
    "ja": "ファイルを上書きしますか？",
    "ko": "파일을 덮어쓸까요?",
    "zh-Hans": "覆盖文件？",
}
PENDING_CHANGE_MESSAGES["save_as.overwrite.prompt"] = {
    "en": "{count} existing file(s) in {folder} will be replaced:\n\n{files}\n\nOverwrite these files?",
    "es": "Se reemplazarán {count} archivo(s) existentes en {folder}:\n\n{files}\n\n¿Sobrescribir estos archivos?",
    "fr": "{count} fichier(s) existant(s) dans {folder} seront remplacés :\n\n{files}\n\nRemplacer ces fichiers ?",
    "de": "{count} vorhandene Datei(en) in {folder} werden ersetzt:\n\n{files}\n\nDiese Dateien überschreiben?",
    "it": "Verranno sostituiti {count} file esistenti in {folder}:\n\n{files}\n\nSovrascrivere questi file?",
    "pt-BR": "{count} arquivo(s) existente(s) em {folder} serão substituídos:\n\n{files}\n\nSobrescrever estes arquivos?",
    "bg": "{count} съществуващи файла в {folder} ще бъдат заменени:\n\n{files}\n\nДа се презапишат ли тези файлове?",
    "nl": "{count} bestaande bestand(en) in {folder} worden vervangen:\n\n{files}\n\nDeze bestanden overschrijven?",
    "pl": "{count} istniejących plików w {folder} zostanie zastąpionych:\n\n{files}\n\nNadpisać te pliki?",
    "ja": "{folder} にある既存のファイル{count}個が置き換えられます：\n\n{files}\n\nこれらのファイルを上書きしますか？",
    "ko": "{folder}의 기존 파일 {count}개가 교체됩니다:\n\n{files}\n\n이 파일들을 덮어쓸까요?",
    "zh-Hans": "{folder} 中的 {count} 个现有文件将被替换：\n\n{files}\n\n是否覆盖这些文件？",
}
PENDING_CHANGE_MESSAGES["save_as.overwrite.cancelled"] = {
    "en": "Save As cancelled. No files were overwritten.",
    "es": "Se canceló Guardar como. No se sobrescribió ningún archivo.",
    "fr": "Enregistrer sous annulé. Aucun fichier n’a été remplacé.",
    "de": "Speichern unter abgebrochen. Es wurden keine Dateien überschrieben.",
    "it": "Salva con nome annullato. Nessun file è stato sovrascritto.",
    "pt-BR": "Salvar como cancelado. Nenhum arquivo foi sobrescrito.",
    "bg": "Запис като е отменен. Не са презаписани файлове.",
    "nl": "Opslaan als geannuleerd. Er zijn geen bestanden overschreven.",
    "pl": "Anulowano Zapisz jako. Żadne pliki nie zostały nadpisane.",
    "ja": "名前を付けて保存をキャンセルしました。ファイルは上書きされていません。",
    "ko": "다른 이름으로 저장을 취소했습니다. 덮어쓴 파일이 없습니다.",
    "zh-Hans": "已取消另存为。未覆盖任何文件。",
}
PENDING_CHANGE_MESSAGES["save_as.overwrite.failed"] = {
    "en": "Save As could not be completed. The previous destination files were restored.",
    "es": "No se pudo completar Guardar como. Se restauraron los archivos de destino anteriores.",
    "fr": "Enregistrer sous n’a pas pu être terminé. Les fichiers de destination précédents ont été restaurés.",
    "de": "Speichern unter konnte nicht abgeschlossen werden. Die vorherigen Zieldateien wurden wiederhergestellt.",
    "it": "Impossibile completare Salva con nome. I file di destinazione precedenti sono stati ripristinati.",
    "pt-BR": "Não foi possível concluir Salvar como. Os arquivos de destino anteriores foram restaurados.",
    "bg": "Запис като не може да завърши. Предишните файлове в местоназначението са възстановени.",
    "nl": "Opslaan als kon niet worden voltooid. De vorige doelbestanden zijn hersteld.",
    "pl": "Nie można ukończyć operacji Zapisz jako. Przywrócono poprzednie pliki docelowe.",
    "ja": "名前を付けて保存を完了できませんでした。保存先の元のファイルを復元しました。",
    "ko": "다른 이름으로 저장을 완료할 수 없습니다. 이전 대상 파일을 복원했습니다.",
    "zh-Hans": "无法完成另存为。已恢复之前的目标文件。",
}
PENDING_CHANGE_MESSAGES["save_as.overwrite.restore_failed"] = {
    "en": "Some destination files could not be restored. Recovery copies are in {folder}.",
    "es": "No se pudieron restaurar algunos archivos de destino. Las copias de recuperación están en {folder}.",
    "fr": "Certains fichiers de destination n’ont pas pu être restaurés. Les copies de récupération se trouvent dans {folder}.",
    "de": "Einige Zieldateien konnten nicht wiederhergestellt werden. Wiederherstellungskopien befinden sich in {folder}.",
    "it": "Impossibile ripristinare alcuni file di destinazione. Le copie di recupero si trovano in {folder}.",
    "pt-BR": "Não foi possível restaurar alguns arquivos de destino. As cópias de recuperação estão em {folder}.",
    "bg": "Някои файлове в местоназначението не могат да бъдат възстановени. Копията за възстановяване са в {folder}.",
    "nl": "Sommige doelbestanden konden niet worden hersteld. Herstelkopieën staan in {folder}.",
    "pl": "Nie można przywrócić niektórych plików docelowych. Kopie odzyskiwania znajdują się w {folder}.",
    "ja": "保存先の一部のファイルを復元できませんでした。復旧用のコピーは {folder} にあります。",
    "ko": "일부 대상 파일을 복원할 수 없습니다. 복구용 사본은 {folder}에 있습니다.",
    "zh-Hans": "无法恢复部分目标文件。恢复副本位于 {folder}。",
}
