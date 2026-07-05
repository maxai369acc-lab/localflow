from localflow.rules import apply_rules


def test_off_level_returns_raw():
    assert apply_rules("um hello", level="off").text == "um hello"


def test_fillers_removed():
    assert apply_rules("um hello uh world", level="light").text == "Hello world"


def test_repeated_words_collapsed():
    assert apply_rules("send send the the file", level="light").text == "Send the file"


def test_backtrack_number_correction():
    r = apply_rules("Let's do coffee at 2 actually 3.", level="light")
    assert r.text == "Let's do coffee at 3."
    assert "backtrack correction" in r.edits


def test_backtrack_across_sentence_boundary():
    # Parakeet-style transcript: ASR closes the sentence before the marker
    r = apply_rules("Let's do coffee at 2. Actually 3", level="light")
    assert r.text == "Let's do coffee at 3"


def test_actually_as_adverb_untouched():
    r = apply_rules("I actually think this is fine", level="light")
    assert r.text == "I actually think this is fine"


def test_scratch_that_removes_previous_sentence():
    r = apply_rules(
        "Send the report on Friday. Scratch that. Send it on Monday morning.",
        level="light")
    assert r.text == "Send it on Monday morning."


def test_spoken_punctuation():
    r = apply_rules(
        "The deadline is Friday period Can we move it question mark",
        level="light")
    assert r.text == "The deadline is Friday. Can we move it?"


def test_spoken_punctuation_with_asr_punctuation():
    # whisper usually punctuates on its own; spoken marks must not double up
    r = apply_rules(
        "The deadline is Friday period. Can we move it question mark?",
        level="light")
    assert r.text == "The deadline is Friday. Can we move it?"


def test_new_line_and_paragraph():
    r = apply_rules(
        "First line new line second line new paragraph third block",
        level="light")
    assert r.text == "First line\nSecond line\n\nThird block"


def test_press_enter_detected():
    r = apply_rules("ship it press enter", level="light")
    assert r.text == "Ship it"
    assert r.send_enter is True


def test_snippet_whole_utterance_exact():
    r = apply_rules("my email", level="light",
                    snippets=[("my email", "max@example.com")])
    assert r.text == "max@example.com"


def test_dictionary_fuzzy_replacement():
    r = apply_rules("i was talking to cheyenne about supabase", level="light",
                    dictionary=["Cheyene", "Supabase"])
    assert r.text == "I was talking to Cheyene about Supabase"


def test_chat_trailing_period_dropped():
    r = apply_rules("sounds good.", level="light", category="chat")
    assert r.text == "Sounds good"


def test_chat_question_mark_kept():
    r = apply_rules("are you coming?", level="light", category="chat")
    assert r.text == "Are you coming?"


# ---- Phase 3/4 additions ----------------------------------------------------

def test_sound_alike_replacement():
    r = apply_rules("I was talking to shy anne earlier", level="light",
                    sound_alikes=[("shy anne", "Cheyene")])
    assert r.text == "I was talking to Cheyene earlier"
    assert "sound-alike correction" in r.edits


def test_file_tag_in_code_apps():
    r = apply_rules("open at app dot py and fix the bug", level="light",
                    category="code")
    assert r.text == "Open @app.py and fix the bug"


def test_file_tag_ignored_outside_code():
    r = apply_rules("we will meet at three dot five", level="light",
                    category="chat")
    assert "@" not in r.text


def test_spoken_form_and_pairs():
    from localflow.devmode import sound_alike_pairs, spoken_form
    assert spoken_form("getUserById") == "get user by id"
    assert spoken_form("save_wav") == "save wav"
    assert ("get user by id", "getUserById") in sound_alike_pairs(["getUserById"])


def test_polish_chunking_splits_long_text():
    from localflow.polish import LlamaClient
    c = LlamaClient(port=1)
    calls = []

    def fake_polish(chunk, **kw):
        calls.append(chunk)
        return chunk.upper(), 1

    c.polish = fake_polish
    text, ms = LlamaClient._polish_chunked(c, "one two three. " * 250)
    assert len(calls) >= 2
    assert ms == len(calls)
    assert "ONE TWO THREE." in text
