#!/usr/bin/env python
import sqlite3
import os
import sys
import collections
import re
from copy import copy

import GeminiQuery
import gemini_utils as util
from gemini_constants import *
import gemini_subjects as subjects

class Site(object):
    def __init__(self, row):
        self.row = row
        self.phased = None
        self.gt = None

    def __eq__(self, other):
        return self.row['chrom'] == other.row['chrom'] and \
               self.row['start'] == other.row['start']


def _add_necessary_columns(args, custom_columns):
    """
    Convenience function to tack on columns that are necessary for
    the functionality of the tool but yet have not been specifically
    requested by the user.
    """
    # we need to add the variant's chrom, start and gene if 
    # not already there.
    if custom_columns.find("gene") < 0:
        custom_columns += ", gene"
    if custom_columns.find("start") < 0:
        custom_columns += ", start"
        
    return custom_columns

def get_compound_hets(args):
    """
    Report candidate compound heterozygous mutations.
    """
    gq = GeminiQuery.GeminiQuery(args.db, include_gt_cols=True)
    idx_to_sample = gq.idx_to_sample
    subjects_dict = subjects.get_subjects(gq.c)
    
    if args.columns is not None:
        custom_columns = _add_necessary_columns(args, str(args.columns))        
        query = "SELECT " + custom_columns + \
                " FROM variants " + \
                " WHERE (is_exonic = 1 or impact_severity != 'LOW') "
    else:
        # report the kitchen sink
        query = "SELECT *" + \
                ", gts, gt_types, gt_phases, gt_depths, \
                gt_ref_depths, gt_alt_depths, gt_quals" + \
                " FROM variants " + \
                " WHERE (is_exonic = 1 or impact_severity != 'LOW') "

    # add any non-genotype column limits to the where clause
    if args.filter:
        query += " AND " + args.filter

    # run the query applying any genotype filters provided by the user.
    gq.run(query)

    comp_hets = collections.defaultdict(lambda: collections.defaultdict(list))

    for row in gq:
        gt_types = row['gt_types']
        gts = row['gts']
        gt_bases = row['gts']
        gt_phases = row['gt_phases']
        
        site = Site(row)

        # track each sample that is heteroyzgous at this site.
        for idx, gt_type in enumerate(gt_types):
            if gt_type == HET:
                sample = idx_to_sample[idx]
                
                if args.only_affected and not subjects_dict[sample].affected:
                    continue

                # sample = "NA19002"
                sample_site = copy(site)
                sample_site.phased = gt_phases[idx]

                # require phased genotypes
                if not sample_site.phased and not args.ignore_phasing:
                    continue

                sample_site.gt = gt_bases[idx]
                # add the site to the list of candidates
                # for this sample/gene
                comp_hets[sample][site.row['gene']].append(sample_site)

    # header
    print "family\tsample\tcomp_het_id\t" + str(gq.header)
    # step 2.  now, cull the list of candidate heterozygotes for each
    # gene/sample to those het pairs where the alternate alleles
    # were inherited on opposite haplotypes.
    comp_het_id = 1
    for sample in comp_hets:
        for gene in comp_hets[sample]:
            for site1 in comp_hets[sample][gene]:
                for site2 in comp_hets[sample][gene]:
                    if site1 == site2:
                        continue

                    # expand the genotypes for this sample
                    # at each site into it's composite
                    # alleles.  e.g. A|G -> ['A', 'G']
                    alleles_site1 = []
                    alleles_site2 = []
                    if not args.ignore_phasing:
                        alleles_site1 = site1.gt.split('|')
                        alleles_site2 = site2.gt.split('|')
                    else:
                        # split on phased (|) or unphased (/) genotypes
                        alleles_site1 = re.split('\||/', site1.gt)
                        alleles_site2 = re.split('\||/', site2.gt)

                    # return the haplotype on which the alternate
                    # allele was observed for this sample at each
                    # candidate het. site.
                    # e.g., if ALT=G and alleles_site1=['A', 'G']
                    # then alt_hap_1 = 1.  if ALT=A, then alt_hap_1 = 0
                    alt_hap_1 = alleles_site1.index(site1.row['alt'])
                    alt_hap_2 = alleles_site2.index(site2.row['alt'])
                    
                    # it is only a true compound heterozygote IFF
                    # the alternates are on opposite haplotypes.
                    if (not args.ignore_phasing and alt_hap_1 != alt_hap_2) \
                        or args.ignore_phasing:
                            print \
                               "\t".join([str(subjects_dict[sample].family_id), 
                                          sample,
                                         str(comp_het_id)]),
                            print site1.row
                            print \
                               "\t".join([str(subjects_dict[sample].family_id), 
                                          sample,
                                          str(comp_het_id)]),
                            print site2.row
                    
                    comp_het_id += 1

def run(parser, args):
    if os.path.exists(args.db):
        try:
            get_compound_hets(args)
        except IOError, e:
            if e.errno != 32:  # ignore SIGPIPE
                raise
        
