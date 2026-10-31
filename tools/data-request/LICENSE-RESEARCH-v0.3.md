# newmean.ai PII Data — Research License Agreement

Version 0.3 · Effective 2026-10-31
Fixed text of this version: https://github.com/newmean-ai/pii/blob/data-license-v0.3/tools/data-request/LICENSE-RESEARCH-v0.3.md · Korean text: https://github.com/newmean-ai/pii/blob/data-license-v0.3/tools/data-request/LICENSE-RESEARCH-v0.3.ko.md
SHA-256 of this file: published at https://github.com/newmean-ai/pii/blob/data-license-v0.3/tools/data-request/LICENSE-RESEARCH-v0.3.md.sha256 and recorded with every acceptance.

This agreement is between **Hoonjae Lee, doing business as newmean.ai** (sole proprietorship, Republic of Korea; business
registration no. 732-22-02446; 708-B99, 100 Crystal-ro, Seohae-gu, Incheon, Republic of Korea) ("newmean.ai", "we",
"us") and the **Licensee** defined in section 1.

## Key terms

This list helps you find the important terms. The full text below governs.

- **Approval first, access later.** The agreement starts when we approve your request; your rights to use the Data and
  your Term start on the Access Date, when we share the Data (sections 2, 10).
- **PII research only.** Use the Data only for non-commercial research on detecting, classifying, masking or anonymizing
  personal information (sections 1, 3, 4).
- **No competing models.** Never use the Data to train general-purpose or foundation models, or AI models or services that
  compete with the providers whose models generated the Data, including Anthropic (section 4).
- **No redistribution.** Do not share the Data, the access link or Derived Data outside your Authorized Users. Papers may show
  up to 20 example rows in total (sections 5, 6).
- **Models stay research-only, also after your access ends.** You may publish model weights only under a non-commercial license
  that passes these limits on, and you must tell us the link (section 7).
- **Report real-looking records.** If a record seems to identify a real person, stop using it and tell us its record ID
  (section 8).
- **Your copy may contain recipient identifiers** that let us trace leaked copies (section 9).
- **Delete the Data** within 14 days after access ends (backups within 90 days) and confirm it (section 10).
- **Termination.** Redistribution, commercial use or competing-model training ends this agreement automatically. Other
  breaches get notice and 30 days to fix (section 11).
- **Free and as is.** Our liability is limited, except for intent or gross negligence (sections 13, 14).
- **Korean law; Seoul courts, non-exclusive.** Your mandatory local protections remain (section 15).

## 1. Definitions

- **Data**: the non-public training and validation files of the newmean.ai PII project (`@newmean-ai/pii`) listed in the
  delivery manifest we share with you, in any version or format, and any copy or part of them.
- **Derived Data**: data made from the Data, such as subsets, samples, relabeled, translated, augmented, filtered or converted
  versions, and any data from which a substantial part of the Data can be reconstructed. Derived Data does not include
  Restricted Models (unless a model is built or tuned to reproduce the Data), aggregate statistics from which the Data cannot be
  reconstructed, publications, or code that contains no Data.
- **Restricted Model**: model weights, adapters or checkpoints that you or an Authorized User train, fine-tune or otherwise
  modify using the Data or Derived Data, and any model trained by distillation from such a model. It does not include (a) a
  pre-existing model that is only evaluated on the Data without being changed, or (b) any model that newmean.ai releases under
  its own license (for example the Apache-2.0 `pii-<size>` models); nothing in this agreement limits your rights under those
  licenses.
- **PII Research**: research, teaching and evaluation concerning the detection, classification, masking, redaction or
  anonymization of personal or sensitive information.
- **Research Use**: PII Research that is not Commercial Use. It includes:
  (a) academic research and teaching;
  (b) research carried out at or funded by a company, including a company research lab, if it is not directed to developing,
  validating or operating a product, service, client deliverable or business process;
  (c) internal research whose results are not published: publication is encouraged but not required, and an unpublished,
  unfinished or unsuccessful study is not a breach;
  (d) evaluating third-party products or services, including commercial ones, for research that you publish or share openly
  (for example a benchmark paper).
- **Commercial Use**: any use (a) to develop, train, test, validate, improve or operate a product or service of you, your
  organization, an affiliate or a client, whether or not it is paid for; (b) in the internal business operations of an
  organization, for example to process its own documents or customer data; (c) for a client or under a paid engagement; or
  (d) otherwise primarily intended for commercial advantage or monetary compensation. Publication and teaching permitted by
  this agreement, and evaluations under Research Use (d), are not Commercial Use merely because they benefit others.
- **Licensee** or **you**: the individual or the legal entity named as the licensee in the approved request (see section 2.3).
- **Authorized Users**: the Licensee (if an individual) and the adult employees, students or researchers of the Licensee who
  work on the Approved Project under the Licensee's control, are bound by written use and confidentiality duties no less
  protective than this agreement, and whose names you give us on request. For a student request, the named supervisor is also
  an Authorized User.
- **Approved Project**: the research described in your approved request.
- **Access Date**: the date on which we first share the Data with you, as stated in our access email.
- **Term**: the period set in section 10.1.

## 2. Acceptance, eligibility and parties

1. You offer to enter into this agreement by ticking the box that names this version in the newmean.ai data request form and
   submitting the request. The agreement takes effect when our approval email reaches the email address of the request.
   Approval may come before the Data is available: the approval email states the expected Access Date, and we email you
   again when we share the Data. Your rights under section 3 and the Term start on the Access Date; until then we owe you no
   delivery by any particular date. We record the version and SHA-256 hash of this text, the time of your acceptance, your
   verified Google account, the Access Date and the files we share.
2. Individual applicants and all Authorized Users must be at least 19 years old, or the age of majority where they live if
   that is higher, and have full legal capacity. We do not accept requests from minors.
3. If you apply for a university, company or other organization, you confirm that you have authority to accept this
   agreement for it. The organization is then the Licensee and is responsible for its Authorized Users. This does not make you
   a personal guarantor of the organization's obligations. If you do not have that authority, or apply as an individual, you
   accept this agreement personally as the Licensee.
4. If you are a student, you must name a supervisor (a faculty member or senior researcher at your institution) in the
   request. You confirm that your supervisor knows about the request, and you will use the Data under their supervision and
   tell them about this agreement.
5. Each renewal and each new version of this agreement needs your express acceptance. If we publish a new version before your
   Access Date, we will send it to you and share the Data only after you accept it; if you do not, this agreement ends
   without any liability on either side.
6. You may withdraw your request or end this agreement before the Access Date at any time by email.

## 3. License

1. Free of charge, for the Term, and only for Research Use within the Approved Project, we grant you a non-exclusive,
   non-transferable, non-sublicensable, worldwide right to:
   (a) store, analyze and process the Data, and make Derived Data, among your Authorized Users;
   (b) train, fine-tune and evaluate Restricted Models;
   (c) publish research results under section 6.
2. Rights not expressly granted are reserved. Commercial Use requires a separate written commercial agreement. We do not
   promise that one will be offered or on what terms.
3. This agreement covers only the Data. It does not change any Apache-2.0 rights in models or SDK code that newmean.ai releases
   separately, or CC BY-NC 4.0 rights in the separately released public test set. Material you obtain under those public
   licenses stays under them.

## 4. Field-of-use limits

The Data was generated with AI models of third-party providers, including Anthropic, whose terms restrict using their outputs
to build competing products or models. Therefore you must not, and must not help anyone else:

1. use the Data, Derived Data or a Restricted Model to train, fine-tune, distill or improve a general-purpose language model,
   chat assistant or foundation model, or any AI model or service that competes with the products or services of the providers
   whose models generated the Data (including Anthropic);
2. use the Data, Derived Data or a Restricted Model for any purpose other than PII Research;
3. use the Data to build, enlarge, offer or sell a dataset or data product that could substitute for the Data. Making Derived
   Data internally for the Approved Project is permitted.

An exception needs our prior written approval and any authorization that the upstream provider requires.

## 5. Other restrictions

1. **No redistribution.** Do not publish, share, sell, sublicense or host the Data, the access link or any Derived Data, in
   whole or in part, outside your Authorized Users, except the excerpts allowed in section 6.
2. **No Commercial Use** of the Data, Derived Data or any Restricted Model.
3. **Service providers and evaluated services.** You may use cloud storage and compute providers to process the Data on your
   behalf under access controls, if they may not use the Data for their own purposes or to train models. For an evaluation
   under Research Use (d), you may send only the records needed to a third-party service to obtain its outputs, and only if
   that service does not keep the Data to train or improve its models (opt out where the service allows). Do not paste the
   Data into AI services that may train on your inputs.
4. **Identifiers and notices.** Do not intentionally remove or alter recipient identifiers (section 9) or notices to defeat
   tracing. Ordinary preprocessing for the Approved Project is permitted.
5. **Access.** Do not circumvent access controls or share your account access. Collaborators outside the Licensee need their
   own approval and acceptance.
6. **Export and sanctions.** Comply with the export-control and sanctions laws that apply to your activities. You confirm that
   you are not located in, or ordinarily resident in, a country or region under comprehensive sanctions, and are not on a
   sanctions list that applies to us or to our service providers.

## 6. Publication and citation

1. You may publish papers, theses, reports, model cards, metrics, analyses and code that contains no Data, without asking us
   first.
2. Across all your publications together, you may show at most 20 unique example records of the Data, plus aggregate
   statistics, subject to privacy and third-party rights. Do not publish any larger part of the Data.
3. Cite the newmean.ai PII technical report (citation in `CITATION.cff` of the project repository) in every publication,
   thesis, report or model card that uses the Data or a Restricted Model.
4. Do not state or imply that newmean.ai endorses your work.

## 7. Restricted Models

1. You may use Restricted Models only for Research Use, during and after the Term. This limit survives expiry and any
   termination.
2. You may publish the weights of a Restricted Model, or offer it to others as a hosted demo or API, only for Research Use and
   only if:
   (a) it is released under CC BY-NC 4.0 or another license that forbids commercial use;
   (b) its license or model card passes on to every recipient the limits in sections 4 and 7.1, and states: "Trained on
   newmean.ai PII data under the newmean.ai Research License. Research use only; no commercial use; not for training
   general-purpose or competing AI models. Commercial licensing: hoonjae@newmean.ai";
   (c) it is not built or tuned to reproduce, regenerate or extract the Data; and
   (d) you send us the link at hoonjae@newmean.ai within 30 days of the release.
   You remain responsible for releases that do not meet these conditions.
3. No ownership of your pre-existing models or your other work passes to us.
4. Commercial use of a Restricted Model needs a separate written commercial agreement. Such an agreement may cover models you
   already trained, but we do not promise one.

## 8. Synthetic data, responsible use and reports

1. **What the Data is.** We designed the Data to be synthetic. We did not give the generator models personal information of
   real individuals as input. Values come from fake-data libraries, random generators and public name lists, or were invented
   by the models; seeded Korean resident registration numbers have deliberately invalid check digits. Even so, a value or a
   combination of values may match a real person by chance, or a model may have reproduced the name of a real person such as
   a public figure. We do not promise that no record relates to a real person.
2. **No re-identification or surveillance.** Do not try to link any record to a real person or to find out personal
   information about anyone. Do not use the Data or a Restricted Model to harvest, profile or surveil people, or to
   re-identify anonymized data. Lawful detection, masking and redaction of personal information within PII Research is
   permitted.
3. **Report.** If you reasonably suspect that a record identifies a real person or infringes third-party rights, immediately
   stop using and disclosing it, restrict access to it, and tell us at hoonjae@newmean.ai using its record ID. Do not email
   the suspected personal information itself and do not investigate the person.
4. **Removal.** Apply our urgent removal instructions without undue delay and in any case within 7 days, and routine
   corrections or removal lists within 30 days, to the Data and your Derived Data, and take down any published excerpt they
   cover. We may suspend deliveries while we investigate.
5. Comply with the laws that apply to you, including privacy law.

## 9. Security, recipient identifiers and privacy

1. Keep the Data on systems with access controls appropriate for confidential research data, available only to Authorized
   Users.
2. Each copy we share may contain random recipient identifiers. We keep the mapping to recipients separately; it is not part
   of the Data. We may use the identifiers to trace unauthorized copies, and we assess suspected misuse on all relevant
   evidence, never on an identifier alone.
3. We process your request and acceptance records as described in our privacy notice: https://github.com/newmean-ai/pii/blob/main/tools/data-request/PRIVACY.md.

## 10. Term, expiry and deletion

1. The Term starts on the Access Date and lasts for the period stated in the access email (180 days unless stated
   otherwise). You may ask to renew it; a renewal needs your express acceptance of the version then in force.
2. When the Term ends or this agreement is terminated, stop using the Data, delete all active copies of the Data and Derived
   Data within 14 days, and delete copies in routine backups that are not accessible for use within 90 days, unless the law
   requires you to keep them. Copies kept under that exception stay protected under this agreement and must not be used.
   Confirm the deletion in writing (a reply to our email is enough) when we ask.
3. Restricted Models you created lawfully, your publications and the excerpts permitted in section 6 may be kept, subject to
   section 7 and to removal under section 8.4.

## 11. Suspension and termination

1. **Automatic termination.** This agreement ends automatically, without notice, if you (a) redistribute the Data or Derived
   Data in breach of section 5.1, (b) use the Data, Derived Data or a Restricted Model for Commercial Use, or (c) breach
   section 4.1. We will confirm the termination by email.
2. **Other breaches.** If you materially breach any other term, we may terminate this agreement by email notice if you do not
   cure the breach within 30 days after receiving the notice, or at once by a notice giving reasons if the breach cannot be
   cured.
3. **Suspension.** We may suspend your access at once where reasonably necessary to deal with a suspected breach, an
   unauthorized disclosure, a report under section 8.3, or a legal or security risk. We will tell you why. If our notice says
   so, pause using the affected Data until we lift the suspension.
4. **End of the programme.** We may end the research data programme by 30 days' email notice. Restricted Models you trained
   before then remain usable under section 7.
5. **Your termination.** You may end this agreement at any time by deleting the Data under section 10.2 and telling us.
6. **Effect on models.** If this agreement ends because of your breach, your right to use Restricted Models created or used
   in connection with that breach ends. Other Restricted Models you created lawfully remain usable under section 7.
7. **Survival.** Sections 4, 5, 6.2–6.4, 7, 8, 9.1–9.2 (for retained copies), 10.2–10.3, 11.6–11.7 and 12–15 continue after
   this agreement ends.

## 12. Rights in the Data

We retain any rights we hold in the Data, including any applicable copyright, database-maker rights and protection under unfair
competition law. Your obligations under this agreement apply as contractual obligations whether or not a record is protected by
any such right. We do not claim exclusive rights in public facts, third-party material or your independently developed work, and
nothing in this agreement creates rights that the law does not otherwise give. As between you and us, you own your research
results and your Restricted Models, subject to this agreement and to third-party rights.

## 13. No warranty

The Data is provided free of charge for experimental research. It is synthetic, may contain errors, label mistakes and values
that match real ones, and is not a complete or representative sample of any population. Section 8.1 describes how we made the
Data. Apart from that, and to the extent permitted by law, we make no promise of accuracy, completeness, fitness for a purpose,
detection performance or non-infringement.

## 14. Liability

1. We are liable only for reasonably foreseeable direct loss caused by our breach of this agreement or our negligence. To the
   extent permitted by law, we are not liable for lost profits, lost data or indirect or consequential loss, and our total
   liability for ordinary negligence under or in connection with this agreement is limited to KRW 1,000,000.
2. Section 14.1 does not exclude or limit liability for intent or gross negligence of newmean.ai or of persons for whose conduct
   we are legally responsible, for death or personal injury, or any other liability that applicable law (including the Korean
   Act on the Regulation of Terms and Conditions) does not allow to be excluded or limited. Nothing in this agreement limits a
   third party's statutory rights.
3. You are responsible for your use and your Authorized Users' use of the Data and Restricted Models.

## 15. General

1. **Law.** This agreement is governed by the laws of the Republic of Korea, without depriving you of protections under
   mandatory law that cannot be waived by agreement.
2. **Courts.** The courts of Seoul (in the first instance, the Seoul Central District Court) have non-exclusive jurisdiction
   over disputes. This does not exclude any court that has jurisdiction under mandatory law, or any court in which either party
   seeks urgent or injunctive relief.
3. **New versions.** We may publish new versions of this agreement. A new version applies only to new approvals and to
   renewals that you expressly accept; your current Term continues under the version you accepted. Corrections and removal
   lists under section 8.4 do not change your rights or obligations under this agreement.
4. **Notices.** Notices are given by email: to you at the address of the request, and to us at hoonjae@newmean.ai.
5. **Transfer.** You may not transfer this agreement without our written consent. We may transfer this agreement, with all our
   rights and obligations, to a successor of the newmean.ai business (for example a company that takes over the business) by
   email notice to you; your rights under this agreement are not reduced by the transfer.
6. **Whole agreement.** This agreement, the approval email and the delivery manifest are the whole agreement on the Data. If a
   part of it is invalid, the rest stays in force. Not enforcing a term is not a waiver.
7. **Language.** The English and Korean texts are both provided before acceptance. For a Licensee who is an individual resident
   in Korea or an entity established in Korea, the Korean text controls; otherwise the English text controls.

Contact: hoonjae@newmean.ai
