from app.config import Settings


def test_default_origins_include_localhost():
    settings = Settings()
    assert "http://localhost:3000" in settings.allowed_origin_list


def test_comma_separated_origins_are_parsed_and_trimmed():
    settings = Settings(
        allowed_origins="https://a.example.com, https://b.example.com ,"
    )
    assert settings.allowed_origin_list == [
        "https://a.example.com",
        "https://b.example.com",
    ]


def test_empty_origins_yield_empty_list():
    settings = Settings(allowed_origins=" , ")
    assert settings.allowed_origin_list == []
