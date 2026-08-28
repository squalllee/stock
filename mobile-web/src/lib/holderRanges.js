export const DEFAULT_LARGE_HOLDER_RANGE = "15-15";

export const LARGE_HOLDER_OPTIONS = [
  { value: "15-15", minLevel: 15, maxLevel: 15, label: "1,000 張以上" },
  { value: "14-14", minLevel: 14, maxLevel: 14, label: "800–1,000 張" },
  { value: "14-15", minLevel: 14, maxLevel: 15, label: "800 張以上" },
  { value: "13-13", minLevel: 13, maxLevel: 13, label: "600–800 張" },
  { value: "13-15", minLevel: 13, maxLevel: 15, label: "600 張以上" },
  { value: "12-15", minLevel: 12, maxLevel: 15, label: "400 張以上" },
  { value: "11-15", minLevel: 11, maxLevel: 15, label: "200 張以上" },
  { value: "10-15", minLevel: 10, maxLevel: 15, label: "100 張以上" },
  { value: "9-15", minLevel: 9, maxLevel: 15, label: "50 張以上" },
  { value: "7-15", minLevel: 7, maxLevel: 15, label: "30 張以上" },
];

export function getLargeHolderOption(value) {
  return LARGE_HOLDER_OPTIONS.find((option) => option.value === value)
    || LARGE_HOLDER_OPTIONS[0];
}

export function largeHolderQuery(option) {
  return `largeLevelMin=${option.minLevel}&largeLevelMax=${option.maxLevel}`;
}
