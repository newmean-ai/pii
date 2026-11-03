export type Label =
  | "PERSON"
  | "PERSON_L2"
  | "EMAIL"
  | "PHONE"
  | "ADDRESS"
  | "ID_NUMBER"
  | "FINANCIAL"
  | "ORG"
  | "USERNAME"
  | "URL"
  | "URL_L2"
  | "DATE_OF_BIRTH"
  | "SECRET";

export interface Span {
  /** [start, end) in UTF-16 code units, as used by String.prototype.slice */
  start: number;
  end: number;
  label: Label | string;
  /** "model", "regex", "model+regex" when both found it, "table" when table mode propagated it down a column, or "block"
   *  for an entry of the option `block` (since 1.4.0) */
  source: "model" | "regex" | "model+regex" | "table" | "block";
}
