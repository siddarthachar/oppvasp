#############################################################################
#
# @file ktest.py @version 4
# This file should be called by <jobfile.sh>
# Last modified: Nov 23, 2010 11:51:03
#
# Example usage:
#
#   import os
#   from oppvasp.vasp.ktest import KTest 
#
#   analyzeOnly = ('vaspcommand' not in os.environ)
#   if analyzeOnly:
#       print "Environment variable 'vaspcommand' not set. Entering analyze-only mode."
#       basedir = os.path.curdir
#       vaspcmd = "ls" #dummy
#       workdir = '/dev/null' #dummy
#   else:
#       basedir = os.environ['SUBMITDIR']
#       vaspcmd = os.environ['vaspcommand']
#       workdir = os.environ['SCRATCH']
#
#   job = KTest(basedir,workdir,vaspcmd)
#   job.start(analyzeOnly)
#
#############################################################################
import os,sys
from batchjob import BatchJob, ManualBatchStep
from oppvasp import utils

class KTest(BatchJob):

    def __init__(self,basedir,workdir,vaspcmd):
        BatchJob.__init__(self,basedir,workdir,vaspcmd)
        self.paramName = 'K' # summary file header

        # Read parameter input
        self.parameterfile = 'ktest.in'
        os.chdir(self.basedir)
        if not os.path.isfile(self.parameterfile):
            print "Parameter-file '%s' not found!" % (self.parameterfile)
            sys.exit(1)
        f = open(self.parameterfile,'r')
        lines = f.readlines()
        f.close()
        aMin = float(lines[0].strip())
        aMax = float(lines[1].strip())
        aStep = float(lines[2].strip())

        paramValues = list(utils.frange6(aMin,aMax,aStep))
         
        POSCAR = open('POSCAR', 'r')
        plines = POSCAR.readlines()
        POSCAR.close()

        for i in range(len(paramValues)):
            plines[1] = '%.4f\n' % (paramValues[i])
            ifile = open('POSCAR.%d' % (i), 'w')
            ifile.writelines(plines)
            ifile.close()
            self.addStep(VolumeTestStep(i,paramValues[i]))

        self.info()

class KTestStep(ManualBatchStep):
    
    def __init__(self,index,paramValue):
        ManualBatchStep.__init__(self,index)
        self.paramValue = paramValue

    def __str__(self):
        return "a = %.3f" % (self.paramValue)
    
    def getName(self):
        return "%.3f" % (self.paramValue)

