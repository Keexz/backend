from app.masking import mask_numbers_and_equations, unmask_text


def test_numbered_hyphenated_sentence_is_not_equation_only():
    text = "1. To determine inter-ethnic unity in 2026."

    result = mask_numbers_and_equations(text)

    assert result.is_equation_only is False
    assert "inter-ethnic unity" in result.masked_text
    assert unmask_text(result.masked_text, result.mapping) == text


def test_actual_equation_is_masked_and_restored():
    text = "The model uses E = mc^2 for comparison."

    result = mask_numbers_and_equations(text)

    assert "[[EQ_0]]" in result.masked_text
    assert unmask_text(result.masked_text, result.mapping) == text


def test_equation_only_expression_is_skipped():
    result = mask_numbers_and_equations("3 + 2 = 5")

    assert result.is_equation_only is True
