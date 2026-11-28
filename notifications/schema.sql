CREATE TABLE IF NOT EXISTS codes (
  provider TEXT NOT NULL, recipient TEXT NOT NULL, digest TEXT NOT NULL UNIQUE,
  issued INTEGER NOT NULL, PRIMARY KEY(provider, recipient)
);
CREATE INDEX IF NOT EXISTS code_expiry ON codes(issued);
CREATE TABLE IF NOT EXISTS devices (
  device TEXT PRIMARY KEY, provider TEXT NOT NULL, recipient TEXT NOT NULL,
  sent INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS recipient_devices ON devices(provider, recipient);
