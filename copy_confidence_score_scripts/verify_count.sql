select c.state_id, s."name" , 
	count(distinct c.id) as chart, 
	count(distinct mhcc.id) as member_hcc, 
	count(distinct mhccold.id) as member_hcc_o, 
	count(distinct hdmi.id) as member_info, 
	count(distinct hdmiold.id) as member_info_o,
	count(distinct cf.id) as file,
	count(distinct cfold.id) as file_o
	from healthcare_data_chart c
	join healthcare_data_chart_state s on s.id = c.state_id 
	join healthcare_data_chart cold on cold.nlp_job_id = c.nlp_job_id and cold.project_id = 2457
	left join healthcare_data_memberreceivedhcc mhcc on mhcc.member_id = c.member_id 
	left join healthcare_data_memberreceivedhcc mhccold on mhccold.member_id = cold.member_id
	left join healthcare_data_member_info hdmi on hdmi.chart_id = c.id 
	left join healthcare_data_member_info hdmiold on hdmiold.chart_id = cold.id 
	left join healthcare_data_chartfile cf on c.id = cf.chart_id 
	left join healthcare_data_chartfile cfold on cfold.chart_id = cold.id 
where c.project_id = 2630 
and c.created_date::date > '2026-07-01'
group by 1,2;