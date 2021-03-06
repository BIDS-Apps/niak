__author__ = 'poquirion'

import shutil
import json
import os
import re
import subprocess
import tempfile
import logging
import time
import yaml


NIAK_CONFIG_PATH = os.getenv("NIAK_CONFIG_PATH", '/local_config')

PSOM_GB_LOCAL = "{}/../lib/psom_gb_vars_local.cbrain".format(os.path.dirname(os.path.realpath(__file__)))

DEBUG = False
if os.getenv("DEBUG", False):
    DEBUG = True


try:
    import psutil
    psutil_loaded = True
except ImportError:
    psutil_loaded = False


try:
    astring = basestring
except NameError:
    astring = str


def num(s):
    try:
        return int(s)
    except ValueError:
        return float(s)

log = logging.getLogger(__file__)


def string(s):
    """
    :param s: A PSOM option
    :return: The right cast for octave
    """
    s.replace("\\", '')
    s = re.match("[\'\"]?([\w+\ -]*)[\'\"]?", s).groups()[0]
    if s in ['true', 'false', 'Inf']:
        return "{0}".format(s)
    return "'{0}'".format(s)

def load_config(yaml_file):
    """
        Tranlate a yaml file into opt and opt.tune pipeline config
    :param yaml_file: a yaml file
    :return: a list of command to be executed before pipeline execution
    """
    with open(yaml_file) as fp:
        config = yaml.load(fp)

    bidon = ['']
    all_cmd = []
    s_cmd = []
    prefix = ''
    def unfold(value):

        if isinstance(value, dict):
            for k, v in value.items():
                bidon[0] = "{0}.{1}".format(bidon[0], k)
                unfold(v)
        else:
            if isinstance(value, astring):
                cast_value = "'{}'".format(value)
            else:
                cast_value = value
            if '{0}'in prefix:
                s_cmd.append("{0}{1}={2}".format(prefix, bidon[0], cast_value))
            else:
                all_cmd.append("{0}{1}={2}".format(prefix, bidon[0], cast_value))
        bidon[0] = ''
    counter = 1
    for k_, v in config.items():
        k = str(k_)
        if k.lower().startswith('group'):
            bidon = ['']
            prefix = 'opt'
            unfold(v)
        else:
            s_cmd = []
            prefix = "opt.tune({0})"
            unfold(v)
            for i, subject in enumerate(unroll_numbers(k), counter):
                all_cmd.append('opt.tune({0}).subject="sub-{1:04d}"'.format(i, subject))
                for line in s_cmd:
                #  TODO make the 4 in {1:04d} a configurable thing
                    all_cmd.append(line.format(i))
            counter = i + 1
    return all_cmd

class BasePipeline(object):
    """
    This is the base class to run PSOM/NIAK pipeline under CBRAIN and the
    BOUTIQUE interface.
    """

    BOUTIQUE_PATH = "{0}/boutique_descriptor"\
        .format(os.path.dirname(os.path.realpath(__file__)))
    BOUTIQUE_INPUTS = "inputs"
    BOUTIQUE_CMD_LINE = "command-line-flag"
    BOUTIQUE_TYPE_CAST = {"Number": num, "String": string, "File": string, "Flag": string}
    BOUTIQUE_TYPE = "type"
    BOUTIQUE_LIST = "list"

    def __init__(self, pipeline_name, folder_in=None, folder_out=None, options=None, **kwargs):

        self.log = logging.getLogger(__file__)
        # literal file name in niak
        self.pipeline_name = pipeline_name

        # The name should be Provided in the derived class
        self._grabber_options = []
        self._pipeline_options = []

        # if os.path.islink(folder_in):
        #     self.folder_in = os.readlink(folder_in)
        # else:
        self.folder_in = folder_in
        self.folder_out = folder_out
        self.octave_options = options

        self.psom_gb_local_path = None

    def psom_gb_vars_local_setup(self):
        """
        This method is crucial to have psom/niak running properly on cbrain.
        :return:
        """
        self.psom_gb_local_path = "{0}/psom_gb_vars_local.m".format(NIAK_CONFIG_PATH)
        try:
            os.makedirs(NIAK_CONFIG_PATH)
        except OSError as e:
            if not os.path.isdir(NIAK_CONFIG_PATH):
                raise e
        shutil.copyfile(PSOM_GB_LOCAL, self.psom_gb_local_path)

    def run(self):
        self.log.debug("Run: {}".format(" ".join(self.octave_cmd)))
        p = None

        self.psom_gb_vars_local_setup()

        try:
            self.log.info("{}".format(" ".join(self.octave_cmd)))
            self.log.info(("{0};\n{1}(files_in, opt);".format(";\n".join(self.octave_options), self.pipeline_name)))
            p = subprocess.Popen(self.octave_cmd)
            p.wait()
        except BaseException as e:
            if p and psutil_loaded:
                parent = psutil.Process(p.pid)
                try:
                    children = parent.children(recursive=True)
                except AttributeError:
                    children = parent.get_children(recursive=True)
                for child in children:
                    child.kill()
                parent.kill()
            self.log.error("Could no process octave command")
            raise e

    @property
    def octave_cmd(self):
        tmp_oct = tempfile.NamedTemporaryFile('w', prefix='niak_script_', suffix='.m', delete=False)
        tmp_oct.write(("{0};\n{1}(files_in, opt);".format(";\n".join(self.octave_options), self.pipeline_name)))
        tmp_oct.close()
        return ["/usr/bin/env", "octave", "--no-gui", "{}".format(tmp_oct.name)]

    @property
    def octave_options(self):

        opt_list = ["opt.folder_out=\'{0}\'".format(self.folder_out)]

        opt_list += self.grabber_construction()

        if self._pipeline_options:
            opt_list += self._pipeline_options

        return opt_list

    @octave_options.setter
    def octave_options(self, options):

        if options is not None:
            # Sort options between grabber (the input file reader) and typecast
            # them with the help of the boutique descriptor
            with open("{0}/{1}.json".format(self.BOUTIQUE_PATH, self.__class__.__name__)) as fp:
                boutique_descriptor = json.load(fp)

            casting_dico = {elem.get(self.BOUTIQUE_CMD_LINE, "")
                            .replace("--opt", "opt").replace("-", "."): [elem.get(self.BOUTIQUE_TYPE),
                                                                         elem.get(self.BOUTIQUE_LIST)]
                            for elem in boutique_descriptor[self.BOUTIQUE_INPUTS]}

            for optk, optv in options.items():


                optv = self.BOUTIQUE_TYPE_CAST[casting_dico[optk][0]](optv)

                # if casting_dico[boutique_opt][1] is True:

                if optk.startswith("--opt_g"):
                    self._grabber_options.append("{0}={1}".format(optk, optv))
                else:
                    self._pipeline_options.append("{0}={1}".format(optk, optv))



    def grabber_construction(self):
        """
        This method needs to be overload to fill the file_in requirement of NIAK
        :return: A list that contains octave string that fill init the file_in variable
        """
        pass



class FmriPreprocess(BasePipeline):

    def __init__(self, subjects=None, func_hint="", anat_hint="", *args,  **kwargs):
        super(FmriPreprocess, self).__init__("niak_pipeline_fmri_preprocess", *args, **kwargs)

        if subjects is not None:
            self.subjects = unroll_numbers(subjects)
        else:
            self.subjects = None
        self.func_hint = func_hint
        self.anat_hint = anat_hint


    def grabber_construction(self):
        """
        :return: A list that contains octave string that fill init the file_in variable

        """
        opt_list = []
        if os.path.isfile("{0}/{1}".format(os.getcwd(), self.folder_in)):
            in_full_path = "{0}/{1}".format(os.getcwd(), self.folder_in)
        else:
            in_full_path = "{0}".format(self.folder_in)
        list_in_dir = os.listdir(in_full_path)
        # TODO Control that with an option
        bids_description = None
        subject_input_list = None
        for f in list_in_dir:
            if f.endswith("dataset_description.json"):
                bid_path = "{0}/{1}".format(in_full_path, f)
                with open(bid_path) as fp:
                    bids_description = json.load(fp)
                break
            elif f.endswith("_demographics.txt"):
                subject_input_list = f
                break

        if subject_input_list:
            opt_list += ["list_subject=fcon_read_demog('{0}/{1}');".format(in_full_path, subject_input_list)]
            opt_list += ["opt_g.path_database='{0}/';".format(in_full_path)]
            opt_list += ["files_in=fcon_get_files(list_subject,opt_g);"]

        elif bids_description:
                opt_list += ["opt_gr = struct();"]
                if self.subjects:
                    self.log.debug("subjects {}".format(self.subjects))
                    opt_list += ["opt_gr.subject_list = {0}".format(self.subjects).replace('[', '{').replace(']', '}')]
                if self.func_hint:
                    self.log.debug("func hint {}".format(self.func_hint))
                    opt_list += ["opt_gr.func_hint = '{0}'".format(self.func_hint)]
                if self.anat_hint:
                    self.log.debug("anat_hint :{}".format(self.anat_hint))
                    opt_list += ["opt_gr.anat_hint = '{0}'".format(self.anat_hint)]

                opt_list += ["files_in=niak_grab_bids('{0}',opt_gr)".format(in_full_path)]

        else:

            # Todo find a good strategy to load subject, to is make it general! --> BIDS
            # % Structural scan
            opt_list += ["files_in.subject1.anat=\'{0}/anat_subject1.mnc.gz\'".format(self.folder_in)]
            # % fMRI run 1
            opt_list += ["files_in.subject1.fmri.session1.motor=\'{0}/func_motor_subject1.mnc.gz\'".format(self.folder_in)]
            opt_list += ["files_in.subject2.anat=\'{0}/anat_subject2.mnc.gz\'".format(self.folder_in)]
            # % fMRI run 1
            opt_list += ["files_in.subject2.fmri.session1.motor=\'{0}/func_motor_subject2.mnc.gz\'".format(self.folder_in)]

        return opt_list


class BaseBids(object):
    """
    This is the base class to run PSOM/NIAK pipeline in a bid app
    """

    BOUTIQUE_PATH = "{0}/boutique_descriptor"\
        .format(os.path.dirname(os.path.realpath(__file__)))
    BOUTIQUE_INPUTS = "inputs"
    BOUTIQUE_CMD_LINE = "command-line-flag"
    BOUTIQUE_TYPE_CAST = {"Number": num, "String": string, "File": string, "Flag": string}
    BOUTIQUE_TYPE = "type"
    BOUTIQUE_LIST = "list"
    PIPELINE_M_FILE = 'pipeline.m'

    def __init__(self, pipeline_name, folder_in, folder_out, config_file=None, options=None):

        # The name should be Provided in the derived class
        self._grabber_options = []
        self._pipeline_options = []
        # literal file name in niak
        self.pipeline_name = pipeline_name

        if os.path.islink(folder_in):
            self.folder_in = os.readlink(folder_in)
        else:
            self.folder_in = folder_in
        self.folder_out_finale = folder_out
        self.octave_options = options


        if config_file:
            self.opt_and_tune_config = load_config(config_file)
        else:
            self.opt_and_tune_config = []

    def run(self):
        log.info(" ".join(self.octave_cmd))
        p = None

        try:
            log.info(self.folder_out)
            p = subprocess.Popen(self.octave_cmd)
            p.wait()
        finally:
            self.rsync_to_finale_folder()

    def rsync_to_finale_folder(self):

        if self.folder_out != self.folder_out_finale:
            log.info("sync {} to {}".format(self.folder_out,self.folder_out_finale))
            rsync = ("rsync -a  --remove-source-files   --exclude logs --exclude report {0}/ {1}"
                     .format(self.folder_out, self.folder_out_finale).split())
            subprocess.call(rsync)

            self.concat_status(self.folder_out, self.folder_out_finale)

    def concat_status(self, src, dest):

        try:
            os.makedirs(os.path.join(dest, "logs"))
        except OSError:
            pass

        l = []
        l.append("new_status = load('{}')".format(os.path.join(src, "logs/PIPE_status.mat")))
        # void all group computation
        l.append("fe = fieldnames(new_status)")
        l.append("for fn =fe' ; if strfind(fn{1},'group');   new_status.(fn{1}) = 'none'   ; end; end")
        l.append("save('{}','-append','-struct','new_status');".format(os.path.join(dest, "logs/PIPE_status.mat")))

        l.append("jobs = load('{}')".format(os.path.join(src, "logs/PIPE_jobs.mat")))
        l.append("save('{}','-append','-struct','jobs');".format(os.path.join(dest, "logs/PIPE_jobs.mat")))
        subprocess.call(self.octave_run(l))

    @property
    def octave_cmd(self):
        m_file = "{0}/{1}".format(self.folder_out, self.PIPELINE_M_FILE)
        with open(m_file,'w') as fp:
            log.info(self.opt_and_tune_config + self.octave_options)
            fp.write(";\n".join(self.opt_and_tune_config + self.octave_options))
            fp.write(";\n{0}(files_in, opt);\n".format(self.pipeline_name))
        return ["/usr/bin/env", "octave", m_file]

    def octave_run(self, options, script_name="octave_run"):

        tmp_oct = tempfile.NamedTemporaryFile('w', prefix=script_name, suffix='.m', delete=False)
        log.info(options)
        tmp_oct.write(";\n".join(options))
        tmp_oct.close()
        return ["/usr/bin/env", "octave", tmp_oct.name]

    @property
    def octave_options(self):

        opt_list = ["opt.folder_out=\'{0}\'".format(self.folder_out)]

        opt_list += self.grabber_construction()

        if self._pipeline_options:
            opt_list += self._pipeline_options

        return opt_list

    @octave_options.setter
    def octave_options(self, options):

        if options is not None:
            # Sort options between grabber (the input file reader) and typecast
            # them with the help of the boutique descriptor
            with open("{0}/{1}.json".format(self.BOUTIQUE_PATH, self.__class__.__name__)) as fp:
                boutique_descriptor = json.load(fp)

            casting_dico = {elem.get(self.BOUTIQUE_CMD_LINE, "")
                            .replace("--opt", "opt").replace("-", "."): [elem.get(self.BOUTIQUE_TYPE),
                                                                         elem.get(self.BOUTIQUE_LIST)]
                            for elem in boutique_descriptor[self.BOUTIQUE_INPUTS]}

            for optk, optv in options.items():

                optv = self.BOUTIQUE_TYPE_CAST[casting_dico[optk][0]](optv)

                # if casting_dico[boutique_opt][1] is True:

                if optk.startswith("--opt_g"):
                    self._grabber_options.append("{0}={1}".format(optk, optv))
                else:
                    self._pipeline_options.append("{0}={1}".format(optk, optv))


    def grabber_construction(self):
        """
        This method needs to be overload to fill the file_in requirement of NIAK
        :return: A list that contains octave string that fill init the file_in variable
        """
        pass


class FmriPreprocessBids(BaseBids):

    def __init__(self, subjects=None, func_hint="", anat_hint="", n_thread=1, group=False
                 , type_scaner="", type_acquisition=None, delay_in_tr=0, suppress_vol=0
                 , hp=0.01, lp=float('inf'), t1_preprocess_nu_correct=50, smooth_vol_fwhm=6, skip_slice_timing=False
                 , *args, **kwargs):
        super(FmriPreprocessBids, self).__init__("niak_pipeline_fmri_preprocess", *args, **kwargs)


        self.func_hint = func_hint
        self.anat_hint = anat_hint

        if subjects is not None:
            self.subjects = unroll_numbers(subjects)
            le_suffix = str(self.subjects[0])
        else:
            self.subjects = None
            le_suffix = 'all'

        if not group:
            if DEBUG:
                self.folder_out = os.path.join(self.folder_out_finale, "results_debug")
            else:
                self.folder_out = tempfile.mkdtemp(prefix='results', suffix=le_suffix, dir=self.folder_out_finale)

            self._pipeline_options.append("opt.size_output = 'all' ")
        else:
            self.folder_out = self.folder_out_finale
            self._pipeline_options.append("opt.psom.flag_update = false")
            self._pipeline_options.append("opt.psom.flag_verbose = 2")


        self._pipeline_options.append("opt.psom.max_queued = {}".format(n_thread))
        self._pipeline_options.append("opt.slice_timing.type_acquisition = '{}'".format(type_acquisition))
        self._pipeline_options.append("opt.slice_timing.type_scanner = '{}'".format(type_scaner))
        self._pipeline_options.append("opt.slice_timing.delay_in_tr = {}".format(delay_in_tr))
        self._pipeline_options.append("opt.slice_timing.suppress_vol = {}".format(suppress_vol))
        self._pipeline_options.append("opt.t1_preprocess.nu_correct.arg = '-distance {}'"
                                      .format(t1_preprocess_nu_correct))
        self._pipeline_options.append("opt.time_filter.hp = {}".format(hp))
        self._pipeline_options.append("opt.time_filter.lp  = {}".format(lp))
        self._pipeline_options.append("opt.smooth_vol.fwh = {}".format(smooth_vol_fwhm))
        if skip_slice_timing:
            self._pipeline_options.append("opt.slice_timing.flag_skip = true")

    def grabber_construction(self):
        """

        :return: A list that contains octave string that fill init the file_in variable


        """
        opt_list = []
        in_full_path = "{1}".format(os.getcwd(), self.folder_in)
        list_in_dir = os.listdir(in_full_path)
        # TODO Control that with an option
        bids_description = None
        subject_input_list = None
        for f in list_in_dir:
            if f.endswith("dataset_description.json"):
                bid_path = "{0}/{1}".format(in_full_path, f)
                with open(bid_path) as fp:
                    bids_description = json.load(fp)

            elif f.endswith("_demographics.txt"):
                subject_input_list = f

        if subject_input_list:
            opt_list += ["list_subject=fcon_read_demog('{0}/{1}')".format(in_full_path, subject_input_list)]
            opt_list += ["opt_g.path_database='{0}/'".format(in_full_path)]
            opt_list += ["files_in=fcon_get_files(list_subject,opt_g)"]

        elif bids_description:
                if self.subjects is not None and len(self.subjects) >= 1:
                    opt_list += ["opt_gr.subject_list = {0}".format(self.subjects).replace('[', '{').replace(']', '}')]
                    opt_list += ["files_in=niak_grab_bids('{0}',opt_gr)".format(in_full_path)]
                else:
                    opt_list += ["files_in=niak_grab_bids('{0}')".format(in_full_path)]

                opt_list += ["opt.slice_timing.flag_skip=true"]

        else:

            # Todo find a good strategy to load subject, to is make it general! --> BIDS
            # % Structural scan
            opt_list += ["files_in.subject1.anat=\'{0}/anat_subject1.mnc.gz\'".format(self.folder_in)]
            # % fMRI run 1
            opt_list += ["files_in.subject1.fmri.session1.motor=\'{0}/func_motor_subject1.mnc.gz\'".format(self.folder_in)]
            opt_list += ["files_in.subject2.anat=\'{0}/anat_subject2.mnc.gz\'".format(self.folder_in)]
            # % fMRI run 1
            opt_list += ["files_in.subject2.fmri.session1.motor=\'{0}/func_motor_subject2.mnc.gz\'".format(self.folder_in)]

        return opt_list

def run_worker(dir, num):
    cmd = ['psom_worker.py', '-d', dir, '-w', str(num)]
    while not os.path.exists("{0}/logs/tmp/".format(dir)):
        # sleep long enough to be last on the race condition TODO (FIND A BETTER WAY TO DO THAT)
        time.sleep(5)
    return subprocess.Popen(cmd)


def bids_validator(path, ignore_warnings=False, ignore_nifti_headers=False):
    """ Runs bids validator on path is one is installed on the machines

    :param path:
    :param ignore_warnings:
    :param ignore_nifti_headers:
    :return:
    """

    cmd = ['bids-validator', '--version']
    try:
        p = subprocess.Popen(cmd)
        out, err = p.communicate()
        log.info("bids-validator version {}".format(out))
    except OSError as e:
        log.warning("cannot validate bids inputs,'bids-validator' is not on the system ")
        return

    cmd = ['bids-validator']
    if ignore_nifti_headers:
        cmd.append('--ignoreNiftiHeaders')
    if ignore_warnings:
        cmd.append('--ignoreWarnings')

    cmd.append(path)

    p = subprocess.Popen(cmd)
    out, err = p.communicate()

    logging.info(out)
    if err:
        logging.error(err)

    if p.returncode:
        logging.warning("bid dataset not valid!")



class BASC(BasePipeline):
    """
    Class to run basc. Only work with outputs from niak preprocessing,
    at least for now.
    """

    def __init__(self, *args, **kwargs):
        super(BASC, self).__init__("niak_pipeline_stability_rest", *args, **kwargs)

    def grabber_construction(self):
        """
        :return:
        """
        file_in = []


        file_in.append("opt_g.min_nb_vol = {0}")
        file_in.append("opt_g.type_files = 'rest'")
        if self.subjects is not None and len(self.subjects) >= 1:
            file_in.append("opt_g.include_subject = {0}".format(self.subjects).replace('[', '{').replace(']', '}'))
        file_in.append("files_in = niak_grab_fmri_preprocess('{0}',opt_g)".format(self.folder_in))


        return file_in



# Set for supported class
SUPPORTED_PIPELINES = {"Niak_fmri_preprocess",
                       "Niak_basc",
                       "Niak_stability_rest"}


def suported(pipeline_name):

    if pipeline_name in SUPPORTED_PIPELINES:
        return True
    else:
        m = 'Pipeline {0} is not in not supported\nMust be part of {1}'.format(pipeline_name, SUPPORTED_PIPELINES)
        logging.warning(m)
        return False


def unroll_numbers(numbers):

    unrolled = []

    def unroll_string(number, unrolled):
        entries = [a[0].split('-') for a in re.findall("([0-9]+((-[0-9]+)+)?)", number)]
        for elem in entries:
            if len(elem) == 1:
                unrolled.append(int(elem[0]))
            elif len(elem) == 2:
                unrolled += [a for a in range(int(elem[0]), int(elem[1])+1)]
            elif len(elem) == 3:
                unrolled += [a for a in range(int(elem[0]), int(elem[1])+1, int(elem[2]))]

    if isinstance(numbers, astring):
        unroll_string(numbers, unrolled)
    else:
        for n in numbers:
            unroll_string(n, unrolled)

    return sorted(list(set(unrolled)))


if __name__ == '__main__':
    # folder_in = "/home/poquirion/test/data_test_niak_mnc1"
    # folder_out = "/var/tmp"
    #
    # basc = BASC(folder_in=folder_in, folder_out=folder_out)
    #
    # print(basc.octave_cmd)

    print(unroll_numbers("1,3,4 15-20, 44, 18-27-2"))
