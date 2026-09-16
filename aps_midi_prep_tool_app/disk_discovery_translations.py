"""Messages for cancellable physical-drive detection."""

_LANGUAGES = ("es", "fr", "de", "it", "pt-BR", "bg", "nl", "pl", "ja", "ko", "zh-Hans")
DISK_DISCOVERY_TRANSLATIONS = {}


def _add(source, translations):
    values = translations.split("|")
    if len(values) != len(_LANGUAGES):
        raise ValueError(f"Incomplete drive detection translation: {source}")
    DISK_DISCOVERY_TRANSLATIONS[source] = dict(zip(_LANGUAGES, values))


_add("Detecting floppy drives...", "Detectando unidades de disquete...|Détection des lecteurs de disquettes...|Diskettenlaufwerke werden gesucht...|Rilevamento delle unità floppy...|Detectando unidades de disquete...|Откриване на флопи устройства...|Diskettestations zoeken...|Wykrywanie napędów dyskietek...|フロッピードライブを検出中...|플로피 드라이브 검색 중...|正在检测软盘驱动器...")
_add("Detecting Floppy Drives", "Detección de unidades de disquete|Détection des lecteurs de disquettes|Diskettenlaufwerke suchen|Rilevamento unità floppy|Detecção de unidades de disquete|Откриване на флопи устройства|Diskettestations zoeken|Wykrywanie napędów dyskietek|フロッピードライブの検出|플로피 드라이브 검색|检测软盘驱动器")
_add("Drive Detection Incomplete", "Detección de unidades incompleta|Détection des lecteurs incomplète|Laufwerkserkennung unvollständig|Rilevamento unità incompleto|Detecção de unidades incompleta|Незавършено откриване на устройства|Detectie van stations onvolledig|Wykrywanie napędów nieukończone|ドライブの検出が完了していません|드라이브 검색 미완료|驱动器检测未完成")
_add(
    "{device}: detection did not finish within {seconds} seconds.",
    "{device}: la detección no terminó en {seconds} segundos.|"
    "{device} : la détection ne s’est pas terminée en {seconds} secondes.|"
    "{device}: Die Erkennung wurde nicht innerhalb von {seconds} Sekunden abgeschlossen.|"
    "{device}: il rilevamento non è terminato entro {seconds} secondi.|"
    "{device}: a detecção não foi concluída em {seconds} segundos.|"
    "{device}: откриването не приключи в рамките на {seconds} секунди.|"
    "{device}: de detectie is niet binnen {seconds} seconden voltooid.|"
    "{device}: wykrywanie nie zakończyło się w ciągu {seconds} sekund.|"
    "{device}: {seconds}秒以内に検出が完了しませんでした。|"
    "{device}: {seconds}초 이내에 검색이 완료되지 않았습니다.|"
    "{device}：检测未在 {seconds} 秒内完成。",
)
_add(
    "Check that a disk is inserted, reconnect an unresponsive USB drive, then try again.",
    "Compruebe que haya un disquete insertado, vuelva a conectar la unidad USB que no responda e inténtelo de nuevo.|"
    "Vérifiez qu’une disquette est insérée, reconnectez le lecteur USB qui ne répond pas, puis réessayez.|"
    "Prüfen Sie, ob eine Diskette eingelegt ist, schließen Sie ein nicht reagierendes USB-Laufwerk erneut an und versuchen Sie es erneut.|"
    "Verifica che sia inserito un dischetto, ricollega l’unità USB che non risponde e riprova.|"
    "Verifique se há um disquete inserido, reconecte a unidade USB que não responde e tente novamente.|"
    "Проверете дали има поставена дискета, свържете отново неотговарящото USB устройство и опитайте пак.|"
    "Controleer of er een diskette is geplaatst, sluit het niet-reagerende USB-station opnieuw aan en probeer het opnieuw.|"
    "Sprawdź, czy włożono dyskietkę, podłącz ponownie napęd USB, który nie odpowiada, i spróbuj jeszcze raz.|"
    "ディスクが挿入されていることを確認し、応答しないUSBドライブを接続し直してから、再試行してください。|"
    "디스켓이 삽입되어 있는지 확인하고 응답하지 않는 USB 드라이브를 다시 연결한 후 재시도하세요.|"
    "请确认已插入软盘，重新连接无响应的 USB 驱动器，然后重试。",
)
_add("Any drives that responded are still available.", "Las unidades que respondieron siguen disponibles.|Les lecteurs ayant répondu restent disponibles.|Laufwerke, die geantwortet haben, sind weiterhin verfügbar.|Le unità che hanno risposto sono ancora disponibili.|As unidades que responderam continuam disponíveis.|Отговорилите устройства все още са достъпни.|Stations die hebben gereageerd blijven beschikbaar.|Napędy, które odpowiedziały, są nadal dostępne.|応答したドライブは引き続き使用できます。|응답한 드라이브는 계속 사용할 수 있습니다.|已响应的驱动器仍然可用。")
_add("If the system is still waiting for the device, restart APS.", "Si el sistema sigue esperando al dispositivo, reinicie APS.|Si le système attend toujours le périphérique, redémarrez APS.|Wenn das System weiterhin auf das Gerät wartet, starten Sie APS neu.|Se il sistema è ancora in attesa del dispositivo, riavvia APS.|Se o sistema ainda estiver aguardando o dispositivo, reinicie o APS.|Ако системата все още чака устройството, рестартирайте APS.|Als het systeem nog op het apparaat wacht, start APS dan opnieuw.|Jeśli system nadal czeka na urządzenie, uruchom APS ponownie.|システムがまだデバイスの応答を待っている場合は、APSを再起動してください。|시스템이 여전히 장치를 기다리고 있다면 APS를 다시 시작하세요.|如果系统仍在等待设备响应，请重新启动 APS。")
