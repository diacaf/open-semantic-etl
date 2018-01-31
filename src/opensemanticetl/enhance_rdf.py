import sys
import logging
import rdflib
from rdflib import Graph
from rdflib import RDFS
from rdflib import Namespace

# define used ontologies / standards / properties
skos = Namespace('http://www.w3.org/2004/02/skos/core#')
owl = Namespace('http://www.w3.org/2002/07/owl#')

import etl
from etl import ETL


# Import RDF graph file granular, not only as a whole single file:
# for every entity (subject) own document with properties (predicates) as facets and its objects as values

class enhance_rdf(object):
	
	def __init__(self, verbose=False):

		self.verbose = verbose

		self.labelProperties = (rdflib.term.URIRef(u'http://www.w3.org/2004/02/skos/core#prefLabel'), rdflib.term.URIRef(u'http://www.w3.org/2000/01/rdf-schema#label'), rdflib.term.URIRef(u'http://www.w3.org/2004/02/skos/core#altLabel'), rdflib.term.URIRef(u'http://www.w3.org/2004/02/skos/core#hiddenLabel'))


	# In which facet to write the predicate / property ?
	def property2facet(self, prop):

		facet = prop + '_ss'

		return facet

	#
	# get all labels, alternate labels / synonyms for the URI/subject, if not there, use subject (=URI) as default
	#

	def get_labels(self, subject):

		labels = []
	
		# append RDFS.label

		# get all labels for this obj
		for label in self.graph.objects(subject=subject, predicate=rdflib.RDFS.label):
			labels.append(label)

		#
		# append SKOS labels
		#
			
		# append SKOS prefLabel
		skos = rdflib.Namespace('http://www.w3.org/2004/02/skos/core#')
		for label in self.graph.objects(subject=subject, predicate=skos['prefLabel']):
			labels.append(label)

		# append SKOS altLabels
		for label in self.graph.objects(subject=subject, predicate=skos['altLabel']):
			labels.append(label)

		# append SKOS hiddenLabels
		for label in self.graph.objects(subject=subject, predicate=skos['hiddenLabel']):
			labels.append(label)

		return labels

	
	#
	# Get indexable full text(s) / label(s) instead of URI references
	#

	def get_values(self, obj):

		values = []

		# since we want full text search we want not to use ID/URI but all labels for indexing
		# if type not literal but URI reference, add label(s)

		if type(obj) == rdflib.URIRef:

			# get labels of this object (therefore it is a subject for getlabels)
			values = self.get_labels(subject=obj)

			if not values: 

				if self.verbose:
					print ( "No label for this object, using URI {}".format(obj) )
		
				values = obj

		elif type(obj) == rdflib.term.Literal:
				values = obj

		# if no values or labels, use the object / URI
		if not values:
			if self.verbose:
				print ( "No label or URI for this object, using object {}".format(obj) )
				print ( "Data type of RDF object: {}".format(type(obj)) )

			values = obj

		return values



	# best/preferred label as title
	def get_preferred_label(self, subject, lang='en'):
		
			preferred_label = self.graph.preferredLabel(subject=subject, lang=lang, labelProperties=self.labelProperties)

			# if no label in preferred language, try with english, if not preferred lang is english yet)
			if not preferred_label and not lang == 'en':
				
				preferred_label = self.graph.preferredLabel(subject=subject, lang='en', labelProperties=self.labelProperties)

			# use label from some other language
			if not preferred_label:
				
				preferred_label = self.graph.preferredLabel(subject=subject, lang='en', labelProperties=self.labelProperties)

			# if no label, use URI
			if preferred_label:
				# since return is tuple with type and label take only the label
				preferred_label = preferred_label[0][1]
			else:
				preferred_label = subject

			return preferred_label


	#
	# ETL knowledge graph to full text search index
	#

	# Index each entity / subject with all its properties/predicates as facets and objects (dereference URIs by their labels) as values


	def etl_graph(self, parameters):

		if self.verbose:
			print("Graph has {} triples.".format(len(self.graph)) )
	
		count_triple = 0
		count_subjects = 0
	
		part_parameters = {}
		part_parameters['plugins'] = []
		part_parameters['export'] = parameters['export']

		etl_processor = ETL()
		etl_processor.verbose = self.verbose
		
		
		# since there can be multiple triples/values for same property,
		# do not overwrite document but add value to existent document & values of the facet/field/property
		part_parameters['add'] = True
		# but not for the field content_type which doesn't change and is not multi valued
		part_parameters['fields_set'] = "content_type"

		# use SPARQL query with distinct to get subjects only once
		res = self.graph.query(
			"""SELECT DISTINCT ?subject
			WHERE {
			?subject ?predicate ?object .
			}""")
	
		for row in res:

			count_subjects += 1
	
			if self.verbose:
				print( "Importing entity / subject {}".format(count_subjects) )

			# get subject of the concept from first column
			subj = row[0]

			if self.verbose:
				print ( "Processing RDF subject {}".format(subj) )

			part_data = {}
			part_data['content_type'] = 'Knowledge graph'
			# subject as URI/ID
			part_parameters['id'] = subj
			part_data['title'] = self.get_preferred_label(subject=subj)


			count_subject_triple = 0

			# get all triples for this subject
			for pred, obj in self.graph.predicate_objects(subject=subj):

				count_triple += 1
				count_subject_triple += 1

				if self.verbose:
					print( "Importing subjects triple {}".format(count_subject_triple) )
					print( "Predicate / property: {}".format(pred) )
					print( "Object / value: {}".format(obj) )


				try:
					#
					# Predicate/property to facet/field
					#

					# set Solr datatype strings so facets not available yet in Solr schema can be inserted automatically (dynamic fields) with right datatype
					
					facet = self.property2facet(prop=pred)

					if self.verbose:
						print ( "Facet: {}".format(facet) )

	
					#
					# get values or labels of this object
					#

					values = self.get_values(obj=obj)

					if self.verbose:
						print ( "Values: {}".format(values) )


					# insert or append value (object of triple) to data
					etl.append(data=part_data, facet=facet, values=values)

				except KeyboardInterrupt:
					raise KeyboardInterrupt
	
				except BaseException as e:
					sys.stderr.write( "Exception while triple {} of subject {}: {}\n".format(count_subject_triple, subj, e) )


	
	
			# index triple
			etl_processor.process( part_parameters, part_data)
	

	def etl_graph_file(self, docid, filename, parameters={}):
		
		self.graph = rdflib.Graph()
		self.graph.parse(filename)

		self.etl_graph(parameters=parameters)


	def process (self, parameters={}, data={} ):
		
		verbose = False
		if 'verbose' in parameters:
			if parameters['verbose']:	
				self.verbose = True
	
		# get parameters
		docid = parameters['id']
		filename = parameters['filename']
		
		if 'content_type' in data:
			mimetype = data['content_type']
		else:
			mimetype = parameters['content_type']

		# if connector returns a list, use only first value (which is the only entry of the list)
		if isinstance(mimetype, list):
			mimetype = mimetype[0]

		# todo: add other formats like turtle
		# if mimetype is graph, call graph import
		if mimetype.lower() == "application/rdf+xml":

			self.etl_graph_file(docid, filename, parameters=parameters)
	
		return parameters, data
