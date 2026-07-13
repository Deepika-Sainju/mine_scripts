-- For member data extraction:
select
    m.first_name                                          AS "MemberFirstName",
    m.last_name                                           AS "MemberLastName",
    m.gender                                              AS "MemberGender",
    mi.source_file                                        AS "MemberMetalLevel",
    m.member_id                                           AS "InputMemberID",
    TO_CHAR(m.dob, 'MM/DD/YYYY')                          AS "MemberDOB",
    CASE WHEN m.custom_1 = 'Yes' THEN '1' ELSE '0' END    AS "MemberESRD"
FROM healthcare_data_member m
JOIN healthcare_data_member_info mi
      ON mi.str_member_id = m.member_id
      AND mi.project_id    = m.project_id
      AND mi.chart_id IN (
        <chart_ids>       
  )
WHERE m.project_id = <project_id>
ORDER BY m.id, mi.source_file;