#!/usr/bin/env python
import sqlite3
import os
import sys
import numpy as np

import gemini_utils as util
from gemini_constants import *


class Subject(object):

    """
    Describe a single subject in the the samples table.
    """
    def __init__(self, row):
        self.sample_id = row['sample_id']
        self.name = row['name']
        self.family_id = row['family_id']
        self.paternal_id = row['paternal_id']
        self.maternal_id = row['maternal_id']
        self.sex = row['sex']
        #phenotype is set to unknown by default
        if row['phenotype'] is None:
            self.phenotype = -9
        else
            self.phenotype = int(row['phenotype'])
        self.ethnicity = row['ethnicity']

        # 1 = unaffected
        # 2 = affected
        # 0 or -9 is unknown.
        # http://pngu.mgh.harvard.edu/~purcell/plink/data.shtml#ped
        if self.phenotype == 2:
            self.affected = True
        elif self.phenotype == 1:
            self.affected = False
        # distinguish unknown from known to be unaffected.
        elif self.phenotype == 0 or self.phenotype == -9:
            self.affected = None

    def __repr__(self):
        return "\t".join([self.name, self.paternal_id,
                          self.maternal_id, str(self.phenotype)])

    def set_father(self):
        self.father = True

    def set_mother(self):
        self.mother = True


class Family(object):

    """
    Describe the relationships among multiple subjects in a family.
    """
    def __init__(self, subjects):
        self.subjects = subjects
        self.father = None
        self.mother = None
        self.family_id = self.subjects[0].family_id
        self.children = []

    def find_parents(self):
        """
        Screen for children with parental ids so that
        we can identify the parents in this family.

        NOTE: assumes at most a 2 generation family.
        """
        self.father_name = None
        self.mother_name = None
        for subject in self.subjects:
            # if mom and dad are found, we know this is the child
            if subject.maternal_id is not None and \
               str(subject.maternal_id) != "-9" and \
               str(subject.maternal_id) != "0" and \
               str(subject.paternal_id) is not None and \
               str(subject.paternal_id) != "-9" and \
               subject.paternal_id != "0":
                self.father_name = str(subject.paternal_id)
                self.mother_name = str(subject.maternal_id)
                self.children.append(subject)

        # now track the actual sampleIds for the parents
        for subject in self.subjects:
            if self.father_name is not None and \
               subject.name == self.father_name:
                self.father = subject
            elif self.mother_name is not None and \
               subject.name == self.mother_name:
                self.mother = subject

        if self.father is not None and self.mother is not None:
            return True
        else:
            return False

    def get_auto_recessive_filter(self):
        """
        Generate an autosomal recessive eval() filter to apply for this family.
        For example:

        '(gt_types[57] == HET and \  # mom
          gt_types[58] == HET and \  # dad
          gt_types[11] == HOM_ALT)'  # affected child
        """

        # identify which samples are the parents in the family.
        # Fail if both parents are not found
        if not self.find_parents():
            return "False"

        # if either parent is affected, this family cannot satisfy
        # a recessive model, as the parents should be carriers.
        if self.father.affected == True or self.mother.affected == True:
            return "False"

        # []---()
        #    |
        #   (*)
        mask = "("
        mask += 'gt_types[' + str(self.father.sample_id - 1) + "] == " + \
            str(HET)
        mask += " and "
        mask += 'gt_types[' + str(self.mother.sample_id - 1) + "] == " + \
            str(HET)
        mask += " and "
        for i, child in enumerate(self.children):
            if child.affected:
                mask += 'gt_types[' + str(child.sample_id - 1) + "] == " + \
                    str(HOM_ALT)
            else:
                mask += 'gt_types[' + str(child.sample_id - 1) + "] != " + \
                    str(HOM_ALT)

            if i < (len(self.children) - 1):
                mask += " and "

        mask += ")"
        return mask

    def get_auto_dominant_filter(self):
        """
        Generate an autosomal dominant eval() filter to apply for this family.
        For example:
        '(
          ((bool(gt_types[57] == HET)         # mom
            != \
            bool(gt_types[58] == HET)) and \  # dad
            gt_types[11] == HET               # affected child
        )'

        NOTE: the bool(dad) != bool(mom) is an XOR requiring that one and
        only one of the parents is heterozygous
        """

        # identify which samples are the parents in the family.
        # Fail if both parents are not found
        if not self.find_parents():
            return "False"
        
        mask = ""

        if self.father.affected is True and self.mother.affected is True:
            # doesn't meet an auto. dominant model if both parents are affected
            # [*]---(*)
            #     |
            #    (*)
            return "False"
        elif ((self.father.affected is False and self.mother.affected is False) 
             or
             (self.father.affected is None and self.mother.affected is None)):
            # if neither parents are affected, or the affection status is 
            # unknown for both, we can just screen for variants where one and 
            # only one of the parents are hets and and the child is also a het
            # []---()
            #    |
            #   (*)
            mask = "((bool("
            mask += 'gt_types[' + str(self.father.sample_id - 1) + "] == " + \
                str(HET)
            mask += ") != "
            mask += 'bool(gt_types[' + \
                    str(self.mother.sample_id - 1) + "] == " + \
                    str(HET)
            mask += ")) and "
            for i, child in enumerate(self.children):
                if child.affected:
                    mask += 'gt_types[' + str(child.sample_id - 1) + "] == " + \
                        str(HET)
                else:
                    mask += 'gt_types[' + str(child.sample_id - 1) + "] == " + \
                        str(HOM_REF)

                if i < (len(self.children) - 1):
                    mask += " and "
            mask += ")"
            return mask
        elif (self.father.affected is True and 
              self.mother.affected is not True):
            # if only Dad is known to be affected, we must enforce
            # that only the affected child and Dad have the 
            # same heterozygous genotype.
            # [*]---()
            #     |
            #    (*)
            mask = "(("
            mask += 'gt_types[' + str(self.father.sample_id - 1) + "] == " + \
                str(HET)
            mask += " and "
            mask += 'gt_types[' + str(self.mother.sample_id - 1) + "] != " + \
                str(HET)
            mask += ") and "
            for i, child in enumerate(self.children):
                if child.affected:
                    mask += 'gt_types[' + str(child.sample_id - 1) + "] == " + \
                          str(HET)
                else:
                    mask += 'gt_types[' + str(child.sample_id - 1) + "] == " + \
                          str(HOM_REF)
                if i < (len(self.children) - 1):
                    mask += " and "
            mask += ")"
            return mask
        elif (self.father.affected is not True 
              and self.mother.affected is True):
            # if only Mom is known to be affected, we must enforce
            # that only the affected child and Mom have the 
            # same heterozygous genotype.
            # []---(*)
            #    |
            #   (*)
            mask = "(("
            mask += 'gt_types[' + str(self.mother.sample_id - 1) + "] == " + \
                str(HET)
            mask += " and "
            mask += 'gt_types[' + str(self.father.sample_id - 1) + "] != " + \
                str(HET)
            mask += ") and "
            for i, child in enumerate(self.children):
                if child.affected:
                    mask += 'gt_types[' + str(child.sample_id - 1) + "] == " + \
                          str(HET)
                else:
                    mask += 'gt_types[' + str(child.sample_id - 1) + "] == " + \
                          str(HOM_REF)
                if i < (len(self.children) - 1):
                    mask += " and "
            mask += ")"
            return mask



    def get_de_novo_filter(self):
        """
        Generate aa de novo mutation eval() filter to apply for this family.
        For example:

        '(gt_types[57] == HOM_REF and \  # mom
          gt_types[58] == HOM_REF and \  # dad
          gt_types[11] == HET)'          # affected child
          
          # [G/G]---(G/G)
          #       |
          #     (A/G)
        """

        # identify which samples are the parents in the family.
        # Fail if both parents are not found
        if not self.find_parents():
            return "False"

        mask = "("

        mask += "("
        mask += 'gt_types[' + str(self.father.sample_id - 1) + "] == " + \
            str(HOM_REF)
        mask += " and "
        mask += 'gt_types[' + str(self.mother.sample_id - 1) + "] == " + \
            str(HOM_REF)
        mask += ")"

        mask += " or "

        mask += "("
        mask += 'gt_types[' + str(self.father.sample_id - 1) + "] == " + \
            str(HOM_ALT)
        mask += " and "
        mask += 'gt_types[' + str(self.mother.sample_id - 1) + "] == " + \
            str(HOM_ALT)
        mask += ")"

        mask += ")"

        mask += " and "
        for i, child in enumerate(self.children):
            if child.affected:
                mask += 'gt_types[' + str(child.sample_id - 1) + "] == " + \
                    str(HET)
            else:
                mask += 'gt_types[' + str(child.sample_id - 1) + "] != " + \
                    str(HOM_REF)

            if i < (len(self.children) - 1):
                mask += " and "

        return mask

    def get_subject_genotype_columns(self):
        """
        Return the indices into the gts numpy array for the parents
        and the children.
        """
        columns = []
        columns.append('gts[' + str(self.father.sample_id - 1) + ']')
        columns.append('gts[' + str(self.mother.sample_id - 1) + ']')
        for child in self.children:
            columns.append('gts[' + str(child.sample_id - 1) + ']')

        return columns

    def get_subject_depth_columns(self):
        """
        Return the indices into the gt_depths numpy array for the parents
        and the children.
        """
        columns = []
        columns.append('gt_depths[' + str(self.father.sample_id - 1) + ']')
        columns.append('gt_depths[' + str(self.mother.sample_id - 1) + ']')
        for child in self.children:
            columns.append('gt_depths[' + str(child.sample_id - 1) + ']')

        return columns

    def get_subject_genotype_labels(self):
        """
        Return header genotype labels for the parents and the children.
        """
        subjects = []
        
        if self.father.affected is True:
            subjects.append(self.father.name + "(father; affected)")
        elif self.father.affected is False:
            subjects.append(self.father.name + "(father; unaffected)")
        elif self.father.affected is None:
            subjects.append(self.father.name + "(father; unknown)")
            
        if self.mother.affected is True:
            subjects.append(self.mother.name + "(mother; affected)")
        elif self.mother.affected is False:
            subjects.append(self.mother.name + "(mother; unaffected)")
        elif self.mother.affected is None:
            subjects.append(self.mother.name + "(mother; unknown)")
            
        # handle the childrem
        for child in self.children:
            if child.affected is True:
                subjects.append(child.name + "(child; affected)")
            elif child.affected is False:
                subjects.append(child.name + "(child; unaffected)")
            elif child.affected is None:
                subjects.append(child.name + "(child; unknown)")

        return subjects

    def get_subject_depth_labels(self):
        """
        Return header depth labels for the parents and the children.
        """
        subjects = []
        subjects.append(self.father.name + "(depth)")
        subjects.append(self.mother.name + "(depth)")
        for child in self.children:
            subjects.append(child.name + "(depth)")

        return subjects


def get_families(c):
    """
    Query the samples table to return a list of Family
    objects that each contain all of the Subjects in a Family.
    """
    query = "SELECT * FROM samples \
             WHERE family_id is not NULL \
             ORDER BY family_id"
    c.execute(query)

    families_dict = {}
    for row in c:
        subject = Subject(row)
        family_id = subject.family_id
        if family_id in families_dict:
            families_dict[family_id].append(subject)
        else:
            families_dict[family_id] = []
            families_dict[family_id].append(subject)

    families = []
    for fam in families_dict:
        family = Family(families_dict[fam])
        families.append(family)
    return families
    
def get_subjects(c):
    """
    Query the samples table to return a dict of subjects.
    
    
    """
    query = "SELECT * FROM samples"
    c.execute(query)

    samples_dict = {}
    for row in c:
        subject = Subject(row)
        sample_name = subject.name
        samples_dict[sample_name] = subject
    return samples_dict
