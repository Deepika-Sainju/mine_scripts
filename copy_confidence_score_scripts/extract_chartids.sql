-- extract
(
    SELECT hdc.chart_id --, r."Coding Status"
    FROM reports.humana_no_hcc_output_sent r
    JOIN healthcare_data_chart hdc
      ON hdc.chart_id = r."Chart ID"
     AND hdc.project_id = 2457
     AND hdc.state_id IN (205, 207)
    WHERE r."Project ID" = 2457
      AND r."Coding Status" = 'No HCC Tech Only'
      AND r."Output Sent Date"::date >= (CURRENT_DATE - INTERVAL '30 days')::date
      AND NOT EXISTS (
            SELECT 1 FROM healthcare_data_chart hdc2
            WHERE hdc2.project_id = 2630
              AND hdc2.nlp_job_id = hdc.nlp_job_id
          )
--      AND EXISTS (
--            SELECT 1 FROM healthcare_data_sentence hds
--            WHERE hds.chart_id = hdc.id
--          )
    ORDER BY r."Output Sent Date"::date DESC
    LIMIT 7000
)
UNION ALL
(
    SELECT hdc.chart_id --, r."Coding Status"
    FROM reports.humana_no_hcc_output_sent r
    JOIN healthcare_data_chart hdc
      ON hdc.chart_id = r."Chart ID"
     AND hdc.project_id = 2457
     AND hdc.state_id IN (205, 207)
    WHERE r."Project ID" = 2457
      AND r."Coding Status" = 'No Net New HCC Tech Only'
      AND r."Output Sent Date"::date >= (CURRENT_DATE - INTERVAL '30 days')::date
      AND NOT EXISTS (
            SELECT 1 FROM healthcare_data_chart hdc2
            WHERE hdc2.project_id = 2630
              AND hdc2.nlp_job_id = hdc.nlp_job_id
          )
--      AND EXISTS (
--            SELECT 1 FROM healthcare_data_sentence hds
--            WHERE hds.chart_id = hdc.id
--          )
    ORDER BY r."Output Sent Date"::date DESC
    LIMIT 7000
);
