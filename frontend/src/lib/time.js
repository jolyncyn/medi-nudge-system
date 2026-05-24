const SGT_TIME_ZONE = "Asia/Singapore";

function parseSgt(value) {
  if (!value) return null;
  if (value instanceof Date) return value;
  const text = String(value);
  const hasTimezone = /(?:Z|[+-]\d{2}:?\d{2})$/.test(text);
  return new Date(hasTimezone ? text : `${text}+08:00`);
}

function partsForSgt(date) {
  const parts = new Intl.DateTimeFormat("en-CA", {
    timeZone: SGT_TIME_ZONE,
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    hour12: false,
  }).formatToParts(date);
  return Object.fromEntries(parts.map((part) => [part.type, part.value]));
}

export function nowSgtDatetimeLocal() {
  const parts = partsForSgt(new Date());
  return `${parts.year}-${parts.month}-${parts.day}T${parts.hour}:${parts.minute}`;
}

export function datetimeLocalToSgtIso(value) {
  return value ? `${value}:00+08:00` : null;
}

export function formatSgtDate(value, options = {}) {
  const date = parseSgt(value);
  if (!date) return "";
  return new Intl.DateTimeFormat(undefined, {
    timeZone: SGT_TIME_ZONE,
    ...options,
  }).format(date);
}

export function formatSgtDateTime(value, options = {}) {
  return formatSgtDate(value, {
    month: "short",
    day: "numeric",
    hour: "2-digit",
    minute: "2-digit",
    ...options,
  });
}
