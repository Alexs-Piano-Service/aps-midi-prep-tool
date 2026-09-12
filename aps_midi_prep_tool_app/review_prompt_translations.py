"""Review invitation text for every supported interface language."""

REVIEW_PROMPT_MESSAGES = {
    "review_prompt.title": {
        "en": "Share your experience", "es": "Comparta su experiencia",
        "fr": "Partagez votre expérience", "de": "Teilen Sie Ihre Erfahrungen",
        "it": "Condividi la tua esperienza", "pt-BR": "Compartilhe sua experiência",
        "bg": "Споделете впечатленията си", "nl": "Deel uw ervaring",
        "pl": "Podziel się opinią", "ja": "ご感想をお聞かせください",
        "ko": "사용 경험을 공유해 주세요", "zh-Hans": "分享您的使用体验",
    },
    "review_prompt.message": {
        "en": "Thank you for using APS MIDI Prep Tool. If you have a moment, would you share a review of your experience? Your feedback helps others decide whether the tool is right for them.\n\nThere is no obligation, and you can choose not to be asked again.",
        "es": "Gracias por usar APS MIDI Prep Tool. Si tiene un momento, ¿podría compartir una reseña de su experiencia? Sus comentarios ayudan a otras personas a decidir si la herramienta les resulta útil.\n\nEs opcional y puede elegir que no se le vuelva a preguntar.",
        "fr": "Merci d’utiliser APS MIDI Prep Tool. Si vous avez un moment, pourriez-vous laisser un avis sur votre expérience ? Vos commentaires aident les autres à décider si cet outil leur convient.\n\nCela reste facultatif et vous pouvez choisir de ne plus recevoir cette demande.",
        "de": "Vielen Dank, dass Sie APS MIDI Prep Tool verwenden. Wenn Sie einen Moment Zeit haben, würden Sie Ihre Erfahrungen in einer Bewertung teilen? Ihr Feedback hilft anderen zu entscheiden, ob das Programm für sie geeignet ist.\n\nDies ist freiwillig. Sie können wählen, nicht erneut gefragt zu werden.",
        "it": "Grazie per aver usato APS MIDI Prep Tool. Se hai un momento, vorresti lasciare una recensione della tua esperienza? Il tuo parere aiuta gli altri a decidere se lo strumento è adatto alle loro esigenze.\n\nÈ facoltativo e puoi scegliere di non ricevere più questa richiesta.",
        "pt-BR": "Obrigado por usar o APS MIDI Prep Tool. Se tiver um momento, gostaria de compartilhar uma avaliação da sua experiência? Sua opinião ajuda outras pessoas a decidir se a ferramenta é adequada para elas.\n\nÉ opcional, e você pode escolher não receber esta solicitação novamente.",
        "bg": "Благодарим ви, че използвате APS MIDI Prep Tool. Ако имате минутка, бихте ли споделили отзив за впечатленията си? Вашето мнение помага на другите да решат дали инструментът е подходящ за тях.\n\nТова е по желание и можете да изберете да не получавате тази покана отново.",
        "nl": "Bedankt dat u APS MIDI Prep Tool gebruikt. Heeft u even tijd om een beoordeling over uw ervaring te delen? Uw feedback helpt anderen te bepalen of het programma bij hen past.\n\nDit is geheel vrijblijvend. U kunt ervoor kiezen deze vraag niet meer te ontvangen.",
        "pl": "Dziękujemy za korzystanie z APS MIDI Prep Tool. Jeśli masz chwilę, czy zechcesz podzielić się opinią o swoich doświadczeniach? Twoja opinia pomoże innym zdecydować, czy to narzędzie jest dla nich odpowiednie.\n\nTo całkowicie dobrowolne. Możesz wybrać, aby więcej nie pytać.",
        "ja": "APS MIDI Prep Tool をご利用いただきありがとうございます。お時間があれば、使用した感想をレビューとして共有していただけませんか？他の方がこのツールを選ぶ際の参考になります。\n\nレビューは任意です。今後この案内を表示しないこともできます。",
        "ko": "APS MIDI Prep Tool을 사용해 주셔서 감사합니다. 잠시 시간을 내어 사용 경험에 대한 리뷰를 남겨 주시겠어요? 여러분의 의견은 다른 분들이 이 도구가 자신에게 적합한지 판단하는 데 도움이 됩니다.\n\n리뷰 작성은 선택 사항이며, 다시 묻지 않도록 설정할 수 있습니다.",
        "zh-Hans": "感谢您使用 APS MIDI Prep Tool。如果您有空，愿意写一条评价，分享您的使用体验吗？您的反馈可以帮助其他人判断这款工具是否适合他们。\n\n评价完全自愿，您也可以选择不再收到此提示。",
    },
    "review_prompt.review": {
        "en": "Write a review", "es": "Escribir una reseña", "fr": "Laisser un avis",
        "de": "Bewertung schreiben", "it": "Scrivi una recensione", "pt-BR": "Escrever uma avaliação",
        "bg": "Напиши отзив", "nl": "Beoordeling schrijven", "pl": "Napisz opinię",
        "ja": "レビューを書く", "ko": "리뷰 작성", "zh-Hans": "撰写评价",
    },
    "review_prompt.later": {
        "en": "Remind me later", "es": "Recordármelo más tarde", "fr": "Me le rappeler plus tard",
        "de": "Später erinnern", "it": "Ricordamelo più tardi", "pt-BR": "Lembrar mais tarde",
        "bg": "Напомни ми по-късно", "nl": "Later herinneren", "pl": "Przypomnij później",
        "ja": "後で通知する", "ko": "나중에 알림", "zh-Hans": "稍后提醒我",
    },
    "review_prompt.never": {
        "en": "Never ask again", "es": "No volver a preguntar", "fr": "Ne plus me demander",
        "de": "Nicht mehr fragen", "it": "Non chiedermelo più", "pt-BR": "Não perguntar novamente",
        "bg": "Не питай отново", "nl": "Niet meer vragen", "pl": "Nie pytaj ponownie",
        "ja": "今後表示しない", "ko": "다시 묻지 않기", "zh-Hans": "不再询问",
    },
}
