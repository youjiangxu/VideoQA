import numpy as np
import os
import h5py
import math

import MovieQA_benchmark as MovieQA
import DataUtil
import ModelUtil


# os.environ["CUDA_DEVICE_ORDER"]="PCI_BUS_ID"   # see issue #152
os.environ["CUDA_VISIBLE_DEVICES"]="0"

import tensorflow as tf


def build_model(input_stories, 
			input_question, size_voc, word_embedding_size, sentence_embedding_size,
			input_answer, common_space_dim,
			answer_index = None, lr=0.01,
			isTest=False):


	with tf.variable_scope('share_embedding_matrix') as scope:
		

		# encode question
		embeded_question_words, mask_q = ModelUtil.getEmbedding(input_question, size_voc, word_embedding_size)
		embeded_question = ModelUtil.getQuestionEncoder(embeded_question_words, sentence_embedding_size, mask_q)

		scope.reuse_variables()
		# encode stories
		embeded_stories_words, mask_s = ModelUtil.getAnswerEmbedding(input_stories, size_voc, word_embedding_size)
		embeded_stories = ModelUtil.getMemoryNetworks(embeded_stories_words, embeded_question, mask_s)


		# encode answers
		embeded_answer_words, mask_a = ModelUtil.getAnswerEmbedding(input_answer, size_voc, word_embedding_size)
		embeded_answer = ModelUtil.getAnswerEncoder(embeded_answer_words, sentence_embedding_size, mask_a)


		# T_s, T_q, T_a = ModelUtil.getMultiModel(embeded_stories, embeded_question, embeded_answer, common_space_dim)
		

		if not isTest:
			# loss = ModelUtil.getTripletLoss(T_s, T_q, T_a, y)
			loss,scores = ModelUtil.getRankingLoss(embeded_stories, embeded_question, embeded_answer, answer_index=answer_index,isTest=isTest)


			
			# train module
			loss = tf.reduce_mean(loss)
			# acc_value = tf.metrics.accuracy(y, embeded_question)
			optimizer = tf.train.GradientDescentOptimizer(lr)
			train = optimizer.minimize(loss)
			return train,loss,scores
		else:
			scores = ModelUtil.getRankingLoss(embeded_stories, embeded_question, embeded_answer, answer_index=answer_index,isTest=isTest)
			return scores

def test_model(model_file, output_file, hf):
	mqa = MovieQA.DataLoader()
	_, test_video_QAs = mqa.get_video_list('test', 'qa_clips')
	# get 'subtitile-based' QA task dataset
	stories, trained_video_QAs = mqa.get_story_qa_data('train', 'subtitle')

	# Create vocabulary
	QA_words, v2i = DataUtil.create_vocabulary(trained_video_QAs, stories, word_thresh=2, v2i={'': 0, 'UNK':1})

	'''
		model parameters
	'''
	size_voc = len(v2i)

	video_feature_dims=2048
	timesteps_v=16 # sequences length for video
	story_shape = (timesteps_v,video_feature_dims)

	timesteps_q=16 # sequences length for question
	timesteps_a=10 # sequences length for anwser
	numberOfChoices = 5 # for input choices, one for correct, one for wrong answer

	word_embedding_size = 300
	sentence_embedding_size = 512
	visual_embedding_dims=512

	common_space_dim = 512
	

	print('building model ...')

	input_stories = tf.placeholder(tf.int32, shape=(None, timesteps_v, video_feature_dims),name='input_stories')
	input_question = tf.placeholder(tf.int32, shape=(None,timesteps_q), name='input_question')
	input_answer = tf.placeholder(tf.int32, shape=(None,numberOfChoices,timesteps_a), name='input_answer')


	scores = build_model(input_stories, visual_embedding_dims, 
			input_question, size_voc, word_embedding_size, sentence_embedding_size,
			input_answer, common_space_dim,
			answer_index=None, lr=0.01,
			isTest=True)
	'''
		configure && runtime environment
	'''
	config = tf.ConfigProto()
	config.gpu_options.per_process_gpu_memory_fraction = 0.2
	# sess = tf.Session(config=tf.ConfigProto(log_device_placement=True))
	config.log_device_placement=False

	sess = tf.Session(config=config)

	init = tf.global_variables_initializer()
	sess.run(init)

	# load model
	saver = tf.train.Saver(sharded=True,max_to_keep=5)
	saver.restore(sess, model_file)


	'''
		parameters
	'''

	batch_size = 64
	

	total_test_qa = len(test_video_QAs)
	num_test_batch = int(round(total_test_qa*1.0/batch_size))
	with open(output_file,'w') as wf:
		with sess.as_default():
			for batch_idx in xrange(num_test_batch):

				batch_qa = test_video_QAs[batch_idx*batch_size:min((batch_idx+1)*batch_size,total_test_qa)]


				data_q,data_a = DataUtil.getBatchTestIndexedQAs(batch_qa,QA_words,v2i, nql=16, nqa=10, numOfChoices=numberOfChoices)
				data_v = DataUtil.getBatchVideoFeature(batch_qa, QA_words, hf, story_shape)

				s = sess.run([scores],feed_dict={input_stories:data_v, input_question:data_q, input_answer:data_a})

				res = np.argmax(s[0],axis=-1)
				for idx,qa in enumerate(batch_qa):
					wf.write('%s %d\n' %(qa.qid,res[idx]))

				print('--Valid--, Batch: %d/%d, Batch_size: %d' %(batch_idx+1,num_test_batch,batch_size))


def train_model(pretrained_model=None):
	task = 'video-based' # video-based or subtitle-based

	mqa = MovieQA.DataLoader()

	# get 'subtitile-based' QA task dataset
	trained_stories, trained_video_QAs = mqa.get_story_qa_data('train', 'subtitle')

	# Create vocabulary
	QA_words, v2i = DataUtil.create_vocabulary(trained_video_QAs, trained_stories, word_thresh=2, v2i={'': 0, 'UNK':1})

	# get 'video-based' QA task training set

	val_stories, val_video_QAs = mqa.get_story_qa_data('val', 'subtitle')




	'''
		model parameters
	'''
	# preprocess trained_stories

	size_voc = len(v2i)


	trained_stories,max_sentences,max_words = DataUtil.normalize_documents(trained_stories, v2i, max_words=20)
	val_stories,_,_ = DataUtil.normalize_documents(val_stories, v2i, max_words=20)

	print('trained_stories... max setences:%d, max words:%d' %(max_sentences,max_words))
	max_sentences = 1500
	story_shape = (max_sentences,max_words)



	timesteps_q=16 # sequences length for question
	timesteps_a=10 # sequences length for anwser
	numberOfChoices = 5 # for input choices, one for correct, one for wrong answer

	word_embedding_size = 300
	sentence_embedding_size = 100
	

	common_space_dim = 512
	

	print('building model ...')

	input_stories = tf.placeholder(tf.int32, shape=(None, max_sentences, max_words),name='input_stories')
	input_question = tf.placeholder(tf.int32, shape=(None,timesteps_q), name='input_question')
	input_answer = tf.placeholder(tf.int32, shape=(None,numberOfChoices,timesteps_a), name='input_answer')

	y = tf.placeholder(tf.float32,shape=(None, numberOfChoices))

	train,loss,scores = build_model(input_stories, 
			input_question, size_voc, word_embedding_size, sentence_embedding_size,
			input_answer, common_space_dim,
			answer_index=y, lr=0.001)

	'''
		configure && runtime environment
	'''
	config = tf.ConfigProto()
	config.gpu_options.per_process_gpu_memory_fraction = 0.5
	# sess = tf.Session(config=tf.ConfigProto(log_device_placement=True))
	config.log_device_placement=False

	sess = tf.Session(config=config)

	init = tf.global_variables_initializer()
	sess.run(init)

	'''
		training parameters
	'''

	batch_size = 32
	total_train_qa = len(trained_video_QAs)
	total_val_qa = len(val_video_QAs)

	num_train_batch = int(round(total_train_qa*1.0/batch_size))
	num_val_batch = int(round(total_val_qa*1.0/batch_size))

	total_epoch = 50
	

	export_path = '/home/xyj/usr/local/saved_model/vqa_baseline/rankloss_subtitle_only'
	if not os.path.exists(export_path):
		os.makedirs(export_path)
		print('mkdir %s' %export_path)

	print('total training samples: %d' %total_train_qa)

	with sess.as_default():
		saver = tf.train.Saver(sharded=True,max_to_keep=total_epoch)
		if pretrained_model is not None:
			saver.restore(sess, pretrained_model)
			print('restore pre trained file:' + pretrained_model)

		for epoch in xrange(total_epoch):
			# # shuffle
			np.random.shuffle(trained_video_QAs)
			for batch_idx in xrange(num_train_batch):

				batch_qa = trained_video_QAs[batch_idx*batch_size:min((batch_idx+1)*batch_size,total_train_qa)]


				data_q,data_a,data_y = DataUtil.getBatchIndexedQAs(batch_qa,QA_words,v2i, nql=16, nqa=10, numOfChoices=numberOfChoices)
				data_s = DataUtil.getBatchIndexedStories(batch_qa,trained_stories,v2i,story_shape)
				_, l, s = sess.run([train,loss,scores],feed_dict={input_stories:data_s, input_question:data_q, input_answer:data_a, y:data_y})

				num_correct = np.sum(np.where(np.argmax(s,axis=-1)==np.argmax(data_y,axis=-1),1,0))
				Acc = num_correct*1.0/batch_size
				print('--Training--, Epoch: %d/%d, Batch: %d/%d, Batch_size: %d Loss: %.5f, Acc: %.5f' %(epoch+1,total_epoch,batch_idx+1,num_train_batch,batch_size,l,Acc))

			print('---------Validation---------')
			total_correct_num = 0
			for batch_idx in xrange(num_val_batch):

				batch_qa = val_video_QAs[batch_idx*batch_size:min((batch_idx+1)*batch_size,total_val_qa)]


				data_q,data_a,data_y = DataUtil.getBatchIndexedQAs(batch_qa,QA_words,v2i, nql=16, nqa=10, numOfChoices=numberOfChoices)
				data_s = DataUtil.getBatchIndexedStories(batch_qa,val_stories,v2i,story_shape)

				l, s = sess.run([loss,scores],feed_dict={input_stories:data_s, input_question:data_q, input_answer:data_a, y:data_y})

				num_correct = np.sum(np.where(np.argmax(s,axis=-1)==np.argmax(data_y,axis=-1),1,0))
				Acc = num_correct*1.0/batch_size
				total_correct_num += num_correct
				print('--Valid--, Epoch: %d/%d, Batch: %d/%d, Batch_size: %d Loss: %.5f, Acc: %.5f' %(epoch+1,total_epoch,batch_idx+1,num_val_batch,batch_size,l,Acc))
			total_correct_num = total_correct_num*1.0/total_val_qa
			print('--Valid--, val acc: %.5f' %(total_correct_num))

			#save model
			save_path = saver.save(sess, export_path+'/'+'E'+str(epoch+1)+'_A'+str(total_correct_num)+'.ckpt')
			print("Model saved in file: %s" % save_path)
		

	
	
				


if __name__ == '__main__':
	isTest = False # True for testing, others for training
	

	if not isTest:
		pretrained_model = '/home/xyj/usr/local/saved_model/vqa_baseline/rankloss_subtitle_only/E7_A0.256384065373.ckpt'
		train_model(pretrained_model=None)
	else:
		model_file = '/home/xyj/usr/local/saved_model/vqa_baseline/rankloss_res/E43_A0.277652370203.ckpt'
		output_file = '/home/xyj/usr/local/predict_result/vqa_baseline/E43_A0.2776.txt'
		test_model(model_file, output_file)

	
	
	
	


	