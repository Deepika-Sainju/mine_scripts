"""Words that must NOT be surrogated inside a PHI span.

Structural/label words + observed clinical terms. Keeps de-identified text
readable and protects clinical content from being replaced when Philter/spaCy
mis-flag a label or medical term as a name.
"""
from __future__ import annotations

KEEP_WORDS = {
    # structural / form labels
    "date", "dates", "of", "birth", "dob", "dos", "doi", "gender", "sex", "age",
    "name", "mrn", "ssn", "npi", "id", "phone", "fax", "email", "address", "author",
    "authored", "on", "signed", "dictated", "by", "page", "service", "visit",
    "encounter", "type", "status", "provider", "patient", "physician", "attending",
    "referring", "rendering", "ordering", "clinician", "doctor", "nurse", "examiner",
    "guarantor", "subscriber", "insured", "member", "claim", "number", "no", "record",
    "report", "medical", "chief", "complaint", "history", "assessment", "plan",
    "diagnosis", "diagnoses", "subjective", "objective", "vitals", "allergies",
    "medications", "written", "primary", "care", "yr", "yrs", "year", "years",
    "male", "female", "to", "for", "and", "the", "am", "pm",
    # observed clinical terms Philter/spaCy mis-flag as names
    "glaucoma", "ecchymosis", "dysphagia", "glomerular", "allergic", "denies",
    "compendium", "regional", "sneeze", "athenahealth",
    # medical eponyms (distinctive — common-surname eponyms like Wilson/Turner/Bell/
    # Graves/Down are intentionally NOT kept, to avoid leaking real patient names)
    "sjogren", "charcot", "wiskott", "aldrich", "crohn", "raynaud", "hodgkin",
    "cushing", "addison", "guillain", "barre", "marfan", "tourette", "asperger",
    "huntington", "kawasaki", "behcet", "wegener", "whipple", "reye", "munchausen",
    "klinefelter", "ehlers", "danlos", "duchenne", "gaucher", "hirschsprung",
    "meniere", "niemann", "fanconi", "wilms", "alzheimer", "parkinson", "epstein",
    "barr", "paget", "alport", "goodpasture", "takayasu", "churg", "strauss",
    "sturge", "peutz", "jeghers", "chiari", "tay", "sachs",
    # conditions / symptoms
    "rheumatoid", "arthritis", "osteoarthritis", "osteoporosis", "fibromyalgia",
    "diabetes", "hypertension", "hypotension", "asthma", "copd", "anemia", "sepsis",
    "pneumonia", "edema", "dyspnea", "syncope", "vertigo", "tinnitus", "pruritus",
    "urticaria", "eczema", "psoriasis", "melanoma", "carcinoma", "lymphoma",
    "leukemia", "sarcoma", "neuropathy", "nephropathy", "retinopathy", "cirrhosis",
    "hepatitis", "gastritis", "colitis", "pancreatitis", "nephritis", "dermatitis",
    "bronchitis", "sinusitis", "tendinitis", "bursitis", "migraine", "seizure",
    "stroke", "embolism", "thrombosis", "aneurysm", "ischemia", "infarction",
    "angina", "arrhythmia", "tachycardia", "bradycardia", "hyperlipidemia",
    "hypothyroidism", "hyperthyroidism", "hypoglycemia", "hyperglycemia", "hematuria",
    "proteinuria", "dysuria", "nausea", "vomiting", "diarrhea", "constipation",
    "fatigue", "fever", "cough", "wheezing", "palpitations", "dizziness", "numbness",
    "tingling", "swelling", "inflammation", "infection", "fracture", "laceration",
    "contusion", "abrasion", "sprain", "strain", "radiculopathy", "stenosis",
    # anatomy / regions
    "lumbar", "spine", "cervical", "thoracic", "sacral", "coccygeal", "femoral",
    "radial", "ulnar", "tibial", "fibular", "humeral", "clavicle", "scapula",
    "sternum", "vertebra", "vertebrae", "cranial", "abdominal", "pelvic", "renal",
    "hepatic", "cardiac", "pulmonary", "gastric", "colon", "duodenum", "ileum",
    "jejunum", "shoulder",
    # labs / chemistry
    "glucose", "creatinine", "hemoglobin", "hematocrit", "platelet", "leukocyte",
    "lymphocyte", "neutrophil", "albumin", "bilirubin", "cholesterol",
    "triglycerides", "sodium", "potassium", "chloride", "calcium", "magnesium",
    "phosphorus",
}
