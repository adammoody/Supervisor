#! /usr/bin/env bash
set -eu

# MLRMBO WORKFLOW
# Main entry point for mlrMBO workflow
# See README.md for more information

# Autodetect this workflow directory
export EMEWS_PROJECT_ROOT=$( cd $( dirname $0 )/.. ; /bin/pwd )
export WORKFLOWS_ROOT=$( cd $EMEWS_PROJECT_ROOT/.. ; /bin/pwd )
export BENCHMARKS_ROOT=$( cd $EMEWS_PROJECT_ROOT/../../../Benchmarks ; /bin/pwd)
export BENCHMARK_DIR=$BENCHMARKS_ROOT/Pilot1/NT3:$BENCHMARKS_ROOT/Pilot1/NT3:$BENCHMARKS_ROOT/Pilot1/P1B1:$BENCHMARKS_ROOT/Pilot1/Combo
export MODEL_SH=$WORKFLOWS_ROOT/common/sh/model.sh
export BENCHMARK_TIMEOUT

SCRIPT_NAME=$(basename $0)

# Source some utility functions used by EMEWS in this script
source $WORKFLOWS_ROOT/common/sh/utils.sh

#source "${EMEWS_PROJECT_ROOT}/etc/emews_utils.sh" - moved to utils.sh

# uncomment to turn on swift/t logging. Can also set TURBINE_LOG,
# TURBINE_DEBUG, and ADLB_DEBUG to 0 to turn off logging
export TURBINE_LOG=1 TURBINE_DEBUG=1 ADLB_DEBUG=1

usage()
{
  echo "COMBO: usage: workflow.sh SITE EXPID CFG_SYS CFG_PRM"
}

if (( ${#} != 4 ))
then
  usage
  exit 1
fi

if ! {
  get_site    $1 # Sets SITE
  get_expid   $2 # Sets EXPID
  get_cfg_sys $3
  get_cfg_prm $4
 }
then
  usage
  exit 1
fi

echo "Running "$MODEL_NAME "workflow" 

# Set PYTHONPATH for BENCHMARK related stuff
PYTHONPATH+=:$BENCHMARK_DIR:$BENCHMARKS_ROOT/common

source_site modules $SITE
source_site langs   $SITE
source_site sched   $SITE

if [[ ${EQR:-} == "" ]]
then
  abort "The site '$SITE' did not set the location of EQ/R: this will not work!"
fi

export TURBINE_JOBNAME="JOB:${EXPID}"

RESTART_FILE_ARG=""
if [[ ${RESTART_FILE:-} != "" ]]
then
  RESTART_FILE_ARG="--restart_file=$RESTART_FILE"
fi

RESTART_NUMBER_ARG=""
if [[ ${RESTART_NUMBER:-} != "" ]]
then
  RESTART_NUMBER_ARG="--restart_number=$RESTART_NUMBER"
fi

R_FILE_ARG=""
if [[ ${R_FILE:-} == "" ]]
then
  R_FILE="mlrMBO1.R"
fi

R_FILE_ARG="--r_file=$R_FILE"

CMD_LINE_ARGS=( -param_set_file=$PARAM_SET_FILE
                -mb=$MAX_BUDGET
                -ds=$DESIGN_SIZE
                -pp=$PROPOSE_POINTS
                -it=$MAX_ITERATIONS
                -model_name=$MODEL_NAME
                -exp_id=$EXPID
                -benchmark_timeout=$BENCHMARK_TIMEOUT
                -site=$SITE
                $RESTART_FILE_ARG
                $RESTART_NUMBER_ARG
                $R_FILE_ARG
              )

USER_VARS=( $CMD_LINE_ARGS )
# log variables and script to to TURBINE_OUTPUT directory
log_script

#Store scripts to provenance
#copy the configuration files and R file (for mlrMBO params) to TURBINE_OUTPUT
cp $WORKFLOWS_ROOT/common/R/$R_FILE $PARAM_SET_FILE $CFG_SYS $CFG_PRM $TURBINE_OUTPUT

# echo's anything following this to standard out
WORKFLOW_SWIFT=workflow.swift
swift-t -n $PROCS \
        ${MACHINE:-} \
        -p -I $EQR -r $EQR \
        -I $WORKFLOWS_ROOT/common/swift \
        -i obj_$SWIFT_IMPL \
        -e LD_LIBRARY_PATH=$LD_LIBRARY_PATH \
        -e TURBINE_RESIDENT_WORK_WORKERS=$TURBINE_RESIDENT_WORK_WORKERS \
        -e RESIDENT_WORK_RANKS=$RESIDENT_WORK_RANKS \
        -e BENCHMARKS_ROOT \
        -e EMEWS_PROJECT_ROOT \
        $( python_envs ) \
        -e TURBINE_OUTPUT=$TURBINE_OUTPUT \
        -e OBJ_RETURN \
        $EMEWS_PROJECT_ROOT/swift/workflow.swift ${CMD_LINE_ARGS[@]}
