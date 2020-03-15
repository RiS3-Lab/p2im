#!/usr/bin/env python3

'''
   P2IM - script to calculate model statistics
   ------------------------------------------------------

   Copyright (C) 2018-2020 RiS3 Lab

   Licensed under the Apache License, Version 2.0 (the "License");
   you may not use this file except in compliance with the License.
   You may obtain a copy of the License at:

     http://www.apache.org/licenses/LICENSE-2.0

'''

from __future__ import division
import json
import csv
import sys
from pprint import pprint

'''
Documentation for this module (namely, getStat function)

Input/Parameter:
model: model json file
groundT: csv file of ground truth
outF: optional parameter, the file where the detailed regster categorization result is written to.

Output/Return value: 
"NUMPER"   number of peripherals accessed by the firmware
"TRA"      number of registers accessed (read, write, or both) by the firmware
"CP"       number of correctly categorizated registers 
"ACC"      register categorization accuracy
"TRR"      number of registers that have ever been READ by the firmware
"TRRC"     number of correctly categorized registers, counting only registers that have ever been READ by the firmware
"TRRW"     number of wrongly categorized registers, counting only registers that have ever been READ by the firmware
"ACCRR"    register categorization accuracy, counting only registers that have ever been READ by the firmware
"MISSREGS" list of missed registers in ground truth file
"INTERRUPTS" interrupts that have ever been enabled by the firmware. This may not be a complete list for all interrupts enabled
'''

def getStatBreak(Regs,groundTruth,base, DRSR=True):
   ModelRegisters=[]
   if DRSR==True:
      
      ModelRegisters={k:v for k, v in Regs.items() if v[3] == base and (v[0]=='SR' or v[0]=='DR')}
   else:
      
      ModelRegisters={k:v for k, v in Regs.items() if v[3] == base }

   totalRegs=len(ModelRegisters)
   correctPrediction=0
   totalRReadCC=0
   totalRReadWC=0
   totalRead=0
   missedRegs=[]
   
   totalDRSR=0
   periname=""
   for key, value in sorted(ModelRegisters.items()):
       try:
          periname=groundTruth[key][3]
          if(groundTruth[key][1]==value[0]):
             #print "C",value[1]
             correctPrediction+=1
             if (int(value[1])==1):
                totalRReadCC+=1
                totalRead+=1
          else:
             #print "W",value[1]
             if(int(value[1])==1):
                 totalRReadWC+=1
                 totalRead+=1
       except KeyError:
          missedRegs.append(key)
   return "PERIPHERAL: ",periname, \
   "BASE: ",base, \
   "TRA: ",totalRegs, \
   "CP: ",correctPrediction, \
   "ACC: ",(correctPrediction/totalRegs*100 if totalRegs >0 else 0), \
   "TRR: ",totalRead, \
   "TRRC: ",totalRReadCC, \
   "TRRW: ",totalRReadWC, \
       "ACCRR: ",(totalRReadCC/totalRead*100 if totalRead>0 else 0), \
       "MISSREGS: ",missedRegs


#   return {"PERIPHERAL":periname, \
#          "BASE":base, \
#          "TRA":totalRegs, \
#          "CP":correctPrediction, \
#          "ACC":(correctPrediction/totalRegs*100 if totalRegs >0 else 0), \
#          "TRR":totalRead, \
#          "TRRC":totalRReadCC, \
#          "TRRW":totalRReadWC, \
#          "ACCRR":(totalRReadCC/totalRead*100 if totalRead>0 else 0), \
#          "MISSREGS":missedRegs}


def getStat0():
    return {"TRA":0, \
          "CP":0, \
          "ACC":0, \
          "TRR":0, \
          "TRRC":0, \
          "TRRW":0, \
          "ACCRR":0, \
          "MISSREGS":0, \
          "MISSCATS":0, \
          "MISCAT_READ":0,\
          "NUMPER":0, \
          "NUMSRSITES":0,\
          "SRSITES":0,\
          "INTERRUPTS":0,\
          "NUMINTERRUPTS":0, \
          "RUN_NUMBER":0}




def getStat(model,groundT,outF=""):
   ModelRegisters={}
   ModelComp=[]
   regBases=[]
   num_peripherals=0
   sr_sites=set()
   interrupts=set()
   srRegsIndexes=set()

   with open(model) as data_file:
      data= json.load(data_file)
      baseInt=0
      reg_size=0 
      modeled=0
      for base in sorted(data["model"]):
         modeled=0
         baseInt=int(base,0)
         baseIntAux=baseInt
         reg_size=data["model"][base]["reg_size"]
         num_peripherals+=1
         index=0

         for regs in data["model"][base]["regs"]:
            regs["address"]=hex(baseInt)
            baseInt=baseInt+reg_size
            if len(regs)>2:
               modeled=1
               if regs["type"]==3:
                  regs["type"]="DR"
               if regs["type"]==2: 
                  regs["type"]="SR"
                  srRegsIndexes.add(index)
               if regs["type"]==4: 
                  regs["type"]="C&SR"
                  srRegsIndexes.add(index)
               if regs["type"]==1:
                  regs["type"]="CR"
               ModelRegisters[regs["address"]]= [regs["type"], regs["read"], regs["write"],base]
            index+=1
         regBases.append(base)

         events = data["model"][base]["events"]
         
         for config in events:
               #print(config)
               for sr_site in data["model"][base]["events"][config]:
                     sr_idxs=data["model"][base]["events"][config][sr_site]["sr_idx"]
                     for sr_idx in sr_idxs:
                         if sr_idx in srRegsIndexes:
                               #print(sr_site)
                               sr_sites.add(sr_site)
                         else:
                               print("Not valid SR site")
      
      for intrs in data["interrupts"]:
            interrupts.add(intrs["excp_num"])


   groundTruth={}
   missedRegs=[]
   with open(groundT) as csv_file:
      csv_reader= csv.reader(csv_file, delimiter=',')
      for row in csv_reader:
         groundTruth[row[0]]=(row[1],row[2],row[3],row[4])
   
   totalRegs=len(ModelRegisters)
   correctPrediction=0
   totalRReadCC=0
   totalRReadWC=0
   totalRead=0
   correct=""
   missedCats=[]
   r_mis_cat=[] # list of registers that are read and mis-categorized
   #base,register,reg name, category, model category,read, write, correct
   table_header = ["Base address","Reg address", "Reg name","Reg cat", "Model Cat", "Read","Write", "Correct cat","Comments GT"]
   ModelComp.append(table_header) 
   r_mis_cat.append(table_header) 
   for key, value in sorted(ModelRegisters.items()):
       try:
          correct="NO"
          if groundTruth[key][1]=="":
             missedCats.append(key)
             print ("missed cat:",key)
          if(groundTruth[key][1]==value[0]):
             correctPrediction+=1
             correct="Yes"
             if (int(value[1])==1):
                totalRReadCC+=1
                totalRead+=1
          else:
             if(int(value[1])==1):
                 totalRReadWC+=1
                 totalRead+=1
                 # register is read and mis-categorized
                 #base,register,reg name, category, model category,read, write, correct
                 r_mis_cat.append([value[3], key, groundTruth[key][0],groundTruth[key][1],value[0],value[1], value[2], correct,groundTruth[key][2]])
          if (outF != ""):
             #base,register,reg name, category, model category,read, write, correct
              ModelComp.append([value[3], key, groundTruth[key][0],groundTruth[key][1],value[0],value[1], value[2], correct,groundTruth[key][2]])
       except KeyError:
          missedRegs.append(key)
          print("missed reg:",key)

   if (outF != ""):
      with open(outF, 'w') as csvfile:
         spamwriter = csv.writer(csvfile, delimiter=',',quotechar='|', quoting=csv.QUOTE_MINIMAL)
         for row in  ModelComp:
            spamwriter.writerow(row)
   print("*************Statistics per peripheral*********************")
   for base in regBases:
      print(getStatBreak(ModelRegisters,groundTruth,base,False))
   print("\n**********Statistics per peripheral for SR and DR**********")

   for base in regBases:
      print(getStatBreak(ModelRegisters,groundTruth,base,True))

   print("SR registers indexes")
   print (srRegsIndexes)
   print("\n***********General statistics**************")
   return {"TRA":totalRegs, \
          "CP":correctPrediction, \
          "ACC":correctPrediction/totalRegs*100 if totalRegs> 0 else 0, \
          "TRR":totalRead, \
          "TRRC":totalRReadCC, \
          "TRRW":totalRReadWC, \
          "ACCRR":totalRReadCC/totalRead*100 if totalRead>0 else 0, \
          "MISSREGS":missedRegs, \
          "MISSCATS":missedCats, \
          "MISCAT_READ":r_mis_cat,\
          "NUMPER":num_peripherals, \
          "NUMSRSITES":len(sr_sites),\
          "SRSITES":sr_sites,\
          "INTERRUPTS":interrupts,\
          "NUMINTERRUPTS":len(interrupts)}






if __name__== "__main__":
   if len(sys.argv)==4:
      print (getStat(sys.argv[1],sys.argv[2],sys.argv[3]))
   else:
      print ("Usage: %s model_f ground_truth_f output_f" % sys.argv[0])
