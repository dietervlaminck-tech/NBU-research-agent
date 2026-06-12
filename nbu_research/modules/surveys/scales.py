"""Validated scale library for the survey builder (v0.2).

Each entry: key -> {"name", "citation", "scale": {min, max, min_label,
max_label}, "items": [str, ...]}. Items use the published wordings; reversed
items are marked with "(R)" in the item text (scoring note for the analyst —
the runner presents them like any other item).

The builder inserts a scale as likert questions tagged ``construct=<key>`` and
records {key: citation} in the survey config under ``scale_citations`` so
methods sections can cite the instruments (see docs/INTERFACES.md).
"""

SCALE_LIBRARY = {
    "uwes9": {
        "name": "UWES-9 — Utrecht Work Engagement Scale (short)",
        "citation": ("Schaufeli, W. B., Bakker, A. B., & Salanova, M. (2006). "
                     "The measurement of work engagement with a short questionnaire: "
                     "A cross-national study. Educational and Psychological "
                     "Measurement, 66(4), 701-716."),
        "scale": {"min": 0, "max": 6, "min_label": "Never", "max_label": "Always (every day)"},
        "items": [
            "At my work, I feel bursting with energy.",
            "At my job, I feel strong and vigorous.",
            "I am enthusiastic about my job.",
            "My job inspires me.",
            "When I get up in the morning, I feel like going to work.",
            "I feel happy when I am working intensely.",
            "I am proud of the work that I do.",
            "I am immersed in my work.",
            "I get carried away when I am working.",
        ],
    },
    "tam_pu": {
        "name": "TAM — Perceived Usefulness",
        "citation": ("Davis, F. D. (1989). Perceived usefulness, perceived ease of "
                     "use, and user acceptance of information technology. MIS "
                     "Quarterly, 13(3), 319-340."),
        "scale": {"min": 1, "max": 7, "min_label": "Extremely unlikely", "max_label": "Extremely likely"},
        "items": [
            "Using the system in my job would enable me to accomplish tasks more quickly.",
            "Using the system would improve my job performance.",
            "Using the system in my job would increase my productivity.",
            "Using the system would enhance my effectiveness on the job.",
            "Using the system would make it easier to do my job.",
            "I would find the system useful in my job.",
        ],
    },
    "tam_peou": {
        "name": "TAM — Perceived Ease of Use",
        "citation": ("Davis, F. D. (1989). Perceived usefulness, perceived ease of "
                     "use, and user acceptance of information technology. MIS "
                     "Quarterly, 13(3), 319-340."),
        "scale": {"min": 1, "max": 7, "min_label": "Extremely unlikely", "max_label": "Extremely likely"},
        "items": [
            "Learning to operate the system would be easy for me.",
            "I would find it easy to get the system to do what I want it to do.",
            "My interaction with the system would be clear and understandable.",
            "I would find the system to be flexible to interact with.",
            "It would be easy for me to become skillful at using the system.",
            "I would find the system easy to use.",
        ],
    },
    "bfi10": {
        "name": "BFI-10 — Big Five Inventory (10-item short version)",
        "citation": ("Rammstedt, B., & John, O. P. (2007). Measuring personality in "
                     "one minute or less: A 10-item short version of the Big Five "
                     "Inventory in English and German. Journal of Research in "
                     "Personality, 41(1), 203-212."),
        "scale": {"min": 1, "max": 5, "min_label": "Disagree strongly", "max_label": "Agree strongly"},
        "items": [
            "I see myself as someone who is reserved. (R)",
            "I see myself as someone who is generally trusting.",
            "I see myself as someone who tends to be lazy. (R)",
            "I see myself as someone who is relaxed, handles stress well. (R)",
            "I see myself as someone who has few artistic interests. (R)",
            "I see myself as someone who is outgoing, sociable.",
            "I see myself as someone who tends to find fault with others. (R)",
            "I see myself as someone who does a thorough job.",
            "I see myself as someone who gets nervous easily.",
            "I see myself as someone who has an active imagination.",
        ],
    },
    "pss4": {
        "name": "PSS-4 — Perceived Stress Scale (4-item short form)",
        "citation": ("Cohen, S., Kamarck, T., & Mermelstein, R. (1983). A global "
                     "measure of perceived stress. Journal of Health and Social "
                     "Behavior, 24(4), 385-396."),
        "scale": {"min": 0, "max": 4, "min_label": "Never", "max_label": "Very often"},
        "items": [
            "In the last month, how often have you felt that you were unable to "
            "control the important things in your life?",
            "In the last month, how often have you felt confident about your "
            "ability to handle your personal problems? (R)",
            "In the last month, how often have you felt that things were going "
            "your way? (R)",
            "In the last month, how often have you felt difficulties were piling "
            "up so high that you could not overcome them?",
        ],
    },
}
