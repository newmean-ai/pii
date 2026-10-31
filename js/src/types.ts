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
  /** "model", "regex", or "model+regex" when both found it */
  source: "model" | "regex" | "model+regex";
}
