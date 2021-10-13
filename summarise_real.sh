
config_arr=( 500 1000 1500 )
rm results2021/real_results.txt
for config_val in "${config_arr[@]}"
do
config="real_${config_val}"
    for method in dor scp propensity vsr tarnet drcrn bmc overlap
    do
        value=`tail -n 1 results2021/${method}_${config}.txt`
        echo "${method} ${config} ${value}" >> results2021/real_results.txt
    done
done
