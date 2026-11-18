let
    ReadCsv = (FileName as text) as table =>
        Table.PromoteHeaders(
            Csv.Document(
                Web.Contents(
                    "https://raw.githubusercontent.com/",
                    [RelativePath = "shashaxiaozi1/steam-promo-roadmap/main/capcom-price-tracker/data/powerbi/" & FileName]
                ),
                [Delimiter = ",", Encoding = 65001, QuoteStyle = QuoteStyle.Csv]
            ),
            [PromoteAllScalars = true]
        ),
    Games = Table.Combine({
        ReadCsv("daily_games.csv"),
        ReadCsv("daily_games_koei_tecmo_history.csv")
    }),
    Typed = Table.TransformColumnTypes(Games, {
        {"date_jst", type date},
        {"publisher", type text},
        {"appid", type text},
        {"game_name", type text},
        {"tracked_edition", type text},
        {"package_id", type text},
        {"sample_count", Int64.Type},
        {"max_discount_percent", Int64.Type},
        {"is_on_sale_any", Int64.Type},
        {"lowest_sale_price_usd", type number},
        {"first_observed_utc", type datetimezone},
        {"last_observed_utc", type datetimezone}
    }, "en-US")
in
    Typed
