"""Translations for the conditional piano overlap dialog."""


def _translations(*values):
    languages = ("es", "fr", "de", "it", "pt-BR", "bg", "nl", "pl", "ja", "ko", "zh-Hans")
    if len(values) != len(languages):
        raise ValueError("Piano overlap translations must cover every language.")
    return dict(zip(languages, values))


PIANO_OVERLAP_TRANSLATIONS = {
    "Overlapping Piano Notes": _translations(
        "Notas de piano superpuestas", "Notes de piano superposées", "Überlappende Klaviernoten",
        "Note di pianoforte sovrapposte", "Notas de piano sobrepostas", "Застъпващи се ноти на пианото",
        "Overlappende pianonoten", "Nakładające się nuty fortepianu", "ピアノ音符の重なり", "겹치는 피아노 음표", "重叠的钢琴音符",
    ),
    "{filename}: {count} same-key note overlap(s) found. Choose how to merge them.": _translations(
        "{filename}: se encontraron {count} superposiciones de notas de la misma tecla. Elige cómo combinarlas.",
        "{filename} : {count} chevauchement(s) de notes de la même touche détecté(s). Choisissez comment les fusionner.",
        "{filename}: {count} Überlappung(en) von Noten derselben Taste gefunden. Wählen Sie das Zusammenführungsverhalten.",
        "{filename}: trovate {count} sovrapposizioni di note dello stesso tasto. Scegli come unirle.",
        "{filename}: encontradas {count} sobreposições de notas da mesma tecla. Escolha como mesclá-las.",
        "{filename}: намерени са {count} застъпвания на ноти за един и същ клавиш. Изберете как да се обединят.",
        "{filename}: {count} overlapping(en) van noten op dezelfde toets gevonden. Kies hoe u ze wilt samenvoegen.",
        "{filename}: znaleziono {count} nakładających się nut tego samego klawisza. Wybierz sposób ich scalania.",
        "{filename}: 同じ鍵盤の音符の重なりが {count} 件見つかりました。統合方法を選択してください。",
        "{filename}: 같은 건반의 음표 겹침이 {count}개 발견되었습니다. 병합 방법을 선택하세요.",
        "{filename}：发现 {count} 处同一琴键的音符重叠。请选择合并方式。",
    ),
    "Smart repair": _translations(
        "Reparación inteligente", "Correction intelligente", "Intelligente Korrektur", "Correzione intelligente",
        "Correção inteligente", "Интелигентна поправка", "Slim herstellen", "Inteligentna naprawa", "スマート修復", "스마트 복구", "智能修复",
    ),
    "Keep attacks — trim overlaps": _translations(
        "Conservar ataques — recortar superposiciones", "Conserver les attaques — raccourcir les chevauchements",
        "Anschläge beibehalten — Überlappungen kürzen", "Mantieni gli attacchi — accorcia le sovrapposizioni",
        "Manter ataques — aparar sobreposições", "Запазване на ударите — скъсяване на застъпванията",
        "Aanslagen behouden — overlappingen inkorten", "Zachowaj uderzenia — skróć nakładające się nuty",
        "打鍵を保持 — 重なりを短縮", "타건 유지 — 겹침 줄이기", "保留击键 — 缩短重叠",
    ),
    "Merge only — keep overlaps": _translations(
        "Solo combinar — conservar superposiciones", "Fusionner uniquement — garder les chevauchements",
        "Nur zusammenführen — Überlappungen beibehalten", "Unisci soltanto — mantieni le sovrapposizioni",
        "Apenas mesclar — manter sobreposições", "Само обединяване — запазване на застъпванията",
        "Alleen samenvoegen — overlappingen behouden", "Tylko scal — zachowaj nakładanie",
        "統合のみ — 重なりを保持", "병합만 — 겹침 유지", "仅合并 — 保留重叠",
    ),
    "Remove long notes covering two or more shorter strikes of the same key. Keep the shortest simultaneous note, then trim overlaps at the next attack. This can remove a long note's leading portion and tail.": _translations(
        "Eliminar notas largas que cubran dos o más golpes cortos de la misma tecla. Conservar la nota simultánea más corta y recortar las superposiciones en el siguiente ataque. Esto puede eliminar el inicio y la cola de una nota larga.",
        "Supprimer les notes longues couvrant au moins deux frappes plus courtes de la même touche. Garder la note simultanée la plus courte, puis raccourcir les chevauchements à l’attaque suivante. Le début et la fin d’une note longue peuvent être supprimés.",
        "Lange Noten entfernen, die mindestens zwei kürzere Anschläge derselben Taste überdecken. Bei gleichzeitigem Beginn die kürzeste Note behalten und Überlappungen am nächsten Anschlag kürzen. Dabei können Anfang und Ende einer langen Note entfallen.",
        "Rimuovi le note lunghe che coprono due o più colpi brevi dello stesso tasto. Mantieni la nota simultanea più corta, poi accorcia le sovrapposizioni all’attacco successivo. Questo può eliminare l’inizio e la coda di una nota lunga.",
        "Remover notas longas que cobrem dois ou mais toques curtos da mesma tecla. Manter a nota simultânea mais curta e aparar sobreposições no próximo ataque. Isso pode remover o início e a cauda de uma nota longa.",
        "Премахва дълги ноти, покриващи поне два по-кратки удара на същия клавиш. Запазва най-кратката едновременно започваща нота и скъсява застъпванията при следващия удар. Началото и краят на дълга нота може да се загубят.",
        "Verwijder lange noten die twee of meer kortere aanslagen op dezelfde toets bedekken. Behoud de kortste gelijktijdige noot en kort overlappingen in bij de volgende aanslag. Dit kan het begin en einde van een lange noot verwijderen.",
        "Usuń długie nuty obejmujące co najmniej dwa krótsze uderzenia tego samego klawisza. Zachowaj najkrótszą nutę o jednoczesnym początku, następnie skróć nakładanie do kolejnego uderzenia. Może to usunąć początek i końcówkę długiej nuty.",
        "同じ鍵盤の短い打鍵を2つ以上覆う長い音符を削除します。同時に始まる音符は最短のものを残し、次の打鍵で重なりを短縮します。長い音符の先頭や末尾が削除される場合があります。",
        "같은 건반의 짧은 타건을 두 개 이상 덮는 긴 음표를 제거합니다. 동시에 시작하는 음표 중 가장 짧은 것을 유지하고 다음 타건에서 겹침을 줄입니다. 긴 음표의 앞부분과 뒷부분이 제거될 수 있습니다.",
        "移除覆盖同一琴键两次或更多短击键的长音符。保留同时开始的最短音符，然后在下次击键处截短重叠。这可能移除长音符的开头和尾部。",
    ),
    "Keep each distinct attack and the longest simultaneous note. Trim overlapping notes to end at the next attack, without moving attacks or resuming tails.": _translations(
        "Conservar cada ataque distinto y la nota simultánea más larga. Recortar las notas superpuestas para que terminen en el siguiente ataque, sin mover ataques ni reanudar colas.",
        "Conserver chaque attaque distincte et la note simultanée la plus longue. Raccourcir les notes superposées jusqu’à l’attaque suivante, sans déplacer les attaques ni reprendre les fins coupées.",
        "Jeden einzelnen Anschlag und bei gleichzeitigem Beginn die längste Note behalten. Überlappende Noten am nächsten Anschlag beenden, ohne Anschläge zu verschieben oder abgeschnittene Enden fortzusetzen.",
        "Mantieni ogni attacco distinto e la nota simultanea più lunga. Accorcia le note sovrapposte fino all’attacco successivo, senza spostare gli attacchi o riprendere le code.",
        "Manter cada ataque distinto e a nota simultânea mais longa. Aparar notas sobrepostas para terminar no próximo ataque, sem mover ataques nem retomar caudas.",
        "Запазва всеки отделен удар и най-дългата едновременно започваща нота. Скъсява застъпващите се ноти до следващия удар, без да мести ударите или да възобновява отрязаните краища.",
        "Behoud elke afzonderlijke aanslag en de langste gelijktijdige noot. Laat overlappende noten eindigen bij de volgende aanslag, zonder aanslagen te verschuiven of afgekapte uiteinden te hervatten.",
        "Zachowaj każde odrębne uderzenie i najdłuższą nutę o jednoczesnym początku. Skróć nakładające się nuty do następnego uderzenia, bez przesuwania uderzeń ani wznawiania końcówek.",
        "異なる各打鍵を保持し、同時に始まる音符は最長のものを残します。打鍵を移動したり末尾を再開したりせず、重なる音符を次の打鍵で終了させます。",
        "각 개별 타건과 동시에 시작하는 가장 긴 음표를 유지합니다. 타건을 이동하거나 잘린 뒷부분을 재개하지 않고 겹치는 음표를 다음 타건에서 끝내도록 줄입니다.",
        "保留每次独立击键及同时开始的最长音符。将重叠音符截短至下次击键，不移动击键时间，也不恢复被截断的尾部。",
    ),
    "Keep the existing merge behavior and all attacks. Overlapping notes may mask repeated piano strikes.": _translations(
        "Conservar el comportamiento de combinación actual y todos los ataques. Las notas superpuestas pueden ocultar golpes repetidos de piano.",
        "Conserver le comportement de fusion actuel et toutes les attaques. Les notes superposées peuvent masquer les frappes répétées du piano.",
        "Das bisherige Zusammenführungsverhalten und alle Anschläge beibehalten. Überlappende Noten können wiederholte Klavieranschläge verdecken.",
        "Mantieni il comportamento di unione attuale e tutti gli attacchi. Le note sovrapposte possono mascherare i colpi ripetuti del pianoforte.",
        "Manter o comportamento de mesclagem existente e todos os ataques. Notas sobrepostas podem encobrir toques repetidos do piano.",
        "Запазва досегашното обединяване и всички удари. Застъпващите се ноти може да прикрият повторните удари на пианото.",
        "Behoud het bestaande samenvoeggedrag en alle aanslagen. Overlappende noten kunnen herhaalde pianoaanslagen maskeren.",
        "Zachowaj dotychczasowy sposób scalania i wszystkie uderzenia. Nakładające się nuty mogą maskować powtarzane uderzenia fortepianu.",
        "従来の統合動作とすべての打鍵を保持します。重なる音符によってピアノの連打が聞こえなくなる場合があります。",
        "기존 병합 동작과 모든 타건을 유지합니다. 겹치는 음표가 반복되는 피아노 타건을 가릴 수 있습니다.",
        "保留现有合并行为和所有击键。重叠音符可能掩盖重复的钢琴击键。",
    ),
    "Use this behavior for all future channel merges": _translations(
        "Usar este comportamiento en todas las futuras combinaciones de canales", "Utiliser ce comportement pour toutes les futures fusions de canaux",
        "Dieses Verhalten für alle künftigen Kanalzusammenführungen verwenden", "Usa questo comportamento per tutte le future unioni di canali",
        "Usar este comportamento em todas as futuras mesclagens de canais", "Използване на това поведение за всички бъдещи обединявания на канали",
        "Dit gedrag voor alle toekomstige kanaalsamenvoegingen gebruiken", "Używaj tego sposobu przy każdym przyszłym scalaniu kanałów",
        "今後のすべてのチャンネル統合でこの動作を使用", "앞으로 모든 채널 병합에 이 동작 사용", "将此行为用于今后的所有通道合并",
    ),
    "Reset Hidden Dialogs will show this choice again.": _translations(
        "Restablecer diálogos ocultos volverá a mostrar esta opción.", "Réinitialiser les dialogues masqués affichera à nouveau ce choix.",
        "„Ausgeblendete Dialoge zurücksetzen“ zeigt diese Auswahl erneut an.", "Ripristina finestre nascoste mostrerà di nuovo questa scelta.",
        "Redefinir diálogos ocultos mostrará esta escolha novamente.", "Нулирането на скритите диалози ще покаже този избор отново.",
        "Verborgen dialogen herstellen toont deze keuze opnieuw.", "Resetowanie ukrytych okien dialogowych ponownie wyświetli ten wybór.",
        "「非表示のダイアログをリセット」で、この選択画面を再表示できます。", "숨긴 대화상자 초기화를 사용하면 이 선택 화면이 다시 표시됩니다.",
        "重置隐藏对话框后，将再次显示此选项。",
    ),
    "Channel merge canceled.": _translations(
        "Combinación de canales cancelada.", "Fusion des canaux annulée.", "Kanalzusammenführung abgebrochen.",
        "Unione dei canali annullata.", "Mesclagem de canais cancelada.", "Обединяването на каналите е отменено.",
        "Kanaalsamenvoeging geannuleerd.", "Scalanie kanałów anulowane.", "チャンネル統合をキャンセルしました。", "채널 병합이 취소되었습니다.", "已取消通道合并。",
    ),
}
