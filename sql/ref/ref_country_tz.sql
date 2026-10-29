-- None of the three countries observes daylight saving time in 2026.
SELECT * FROM VALUES
  ('MX', 'America/Mexico_City', 'MXN'),
  ('CO', 'America/Bogota', 'COP'),
  ('BR', 'America/Sao_Paulo', 'BRL')
  AS t(country, time_zone, currency)
