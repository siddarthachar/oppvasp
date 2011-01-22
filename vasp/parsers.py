"""
This file contains classes to easily extract information from VASP input
and output files. Functions are added as needed, and only a small 
subset of the total information available in the input and output files can 
currently be extracted using the classes in this file.

When time allows for it, functions to extract the same information from
both vasprun.xml and OUTCAR type files are implemented. Usually, extracting
data from vasprun.xml files are easier due to the excellent Python xml
parsers available, but it is believed that extracting data from OUTCAR files 
may be faster for very large files. 

Note that this file contains code originally written by olem
"""

from copy import copy
import sys,re,math,os
from StringIO import StringIO
import numpy as np

from oppvasp import getAtomicNumberFromSymbol
from oppvasp.md import Trajectory

# Optional:
imported = { 'progressbar' : False, 'psutil' : False, 'lxml' : False }

try:
    from lxml import etree
    imported['lxml'] = True
    # Useful reading: 
    # - http://codespeak.net/lxml/parsing.html
    # - http://www.ibm.com/developerworks/xml/library/x-hiperfparse/
except ImportError:
    print "Info: Module 'lxml' is not available"

try:
    from progressbar import ProgressBar, Percentage, Bar, ETA, FileTransferSpeed, \
        RotatingMarker, ReverseBar, SimpleProgress
    imported['progressbar'] = True
except ImportError:
    print "Info: Module 'progressbar' is not available"

try:
    import psutil 
    imported['psutil'] = True
except ImportError:
    print "Info: Module 'psutil' is not available"




class myFile(object):
    def __init__(self, filename):
        self.f = open(filename)

    def read(self, size=None):
        # zap control characters that invalidates the xml
        #return self.f.next().replace('\x1e', '').replace('some other bad character...' ,'')
        return re.sub('[\x00-\x09\x0B-\x1F]','',self.f.next())



def print_memory_usage():
    if imported['psutil']:
        p = psutil.Process(os.getpid())
        rss,vms = p.get_memory_info()
        print "Physical memory: %.1f MB" % (rss/1024.**2)

class IterativeVasprunParser:
    """
    Parser for very large vasprun.xml files, based on iterative xml parsing.
    The functionality of this parser is limited compared to VasprunParser.
    """
    
    def __init__(self, filename = 'vasprun.xml', verbose = False):
        
        if not imported['lxml']:
            print "Error: The module 'lxml' is needed!"
            sys.exit(1)
        
        self.filename = filename
        self.verbose = verbose
        print_memory_usage()
        
        # read beginning of file to find number of ionic steps (NSW) and timestep (POTIM)
        self.incar = self._find_first_instance('incar', self._incar_tag_found)
        self.nsw = int(self.incar.xpath("i[@name='NSW']")[0].text)

        # should make a try clause
        self.potim = float(self.incar.xpath("i[@name='POTIM']")[0].text)

        self.atoms = self._find_first_instance('atominfo',self._get_atoms)
        self.natoms = len(self.atoms)

        try:
            self.nsw
            #print "Number of ionic steps: %d" % (self.nsw) 
        except AttributeError:
            print "Could not find incar:NSW in vasprun.xml"
            sys.exit(1)

    def _incar_tag_found(self, elem):
        return copy(elem)
    
    def _get_atoms(self, elem):
        atoms = []
        for rc in elem.xpath("array[@name='atoms']/set/rc"):
            atoms.append(getAtomicNumberFromSymbol(rc[0].text))
        return np.array(atoms, dtype=int)

    def _fast_iter(self, context, func):
        for event, elem in context:
            func(elem)
            elem.clear()
            while elem.getprevious() is not None:
                del elem.getparent()[0]
        del context

    def _find_first_instance(self, tag, func):
        context = etree.iterparse(self.filename, tag=tag)
        for event, elem in context:
            ret = func(elem)
            elem.clear()
            while elem.getprevious() is not None:
                del elem.getparent()[0]
            break
        del context
        return ret

    def get_num_ionic_steps(self):
        """ Returns the number of ionic steps """
        return self.nsw

    def get_num_atoms(self):
        """ Returns the number of atoms """
        return self.natoms

    def get_atoms(self):
        """ Returns an array with the types of the atoms """
        return self.atoms

    def _get_initial_positions(self,elem):
        basis= elem.xpath("crystal/varray[@name='basis']/v")
        basis = [[float(x) for x in p.text.split()] for p in basis]

        pos = elem.xpath("varray[@name='positions']/v")
        pos = [[float(x) for x in p.text.split()] for p in pos]

        vel = elem.xpath("varray[@name='velocities']/v")
        vel = [[float(x) for x in p.text.split()] for p in vel]

        return { 'basis': basis, 'positions': pos, 'velocities': vel }

    def get_initial_structure(self):
        """
        Returns a (N,3) numpy array with the position of all the atoms at the beginning.
        """
        return self._find_first_instance('structure',self._get_initial_positions) 

    def _calculation_tag_found(self, elem):

        bas = elem.xpath("structure/crystal/varray[@name='basis']/v")
        self.trajectory.set_basis(self.step_no, np.array([[float(x) for x in b.text.split()] for b in bas]))

        if self.trajectory.num_atoms == 1:
            pos = elem.xpath("structure/varray[@name='positions']/v[%d]" % (self.atom_no+1))
        else:
            pos = elem.xpath("structure/varray[@name='positions']/v")
        pos = [[float(x) for x in ap.text.split()] for ap in pos]
        self.trajectory.set_positions(self.step_no, pos)
        
        e_kin = elem.xpath("energy/i[@name='kinetic']")
        if e_kin:
            self.trajectory.set_e_kinetic(self.step_no, float(e_kin[0].text))
        
        e_pot = elem.xpath("energy/i[@name='e_fr_energy']")
        self.trajectory.set_e_total(self.step_no, float(e_pot[0].text))

        self.step_no += 1
        if imported['progressbar']:
            self.pbar.update(self.step_no)
        #print pos

    def _get_trajectories(self):
        atoms = self.get_atoms()
        self.trajectory = Trajectory(num_steps = self.nsw, timestep = self.potim, atoms = atoms)
        self.step_no = 0
        status_text = "Parsing %.2f MB... " % (os.path.getsize(self.filename)/1024.**2)
        if imported['progressbar']:
            self.pbar = ProgressBar(widgets=[status_text,Percentage()], maxval = self.nsw+1).start()
        
        parser = etree.XMLParser()
        context = etree.iterparse(self.filename, tag='calculation')
        try:
            self._fast_iter(context, self._calculation_tag_found)
        except etree.XMLSyntaxError:
            for e in parser.error_log:
                print "Warning: "+e.message

        if imported['progressbar']:
            self.pbar.finish()
        print "Found %d out of %d steps" % (self.step_no,self.nsw)
        self.trajectory.update_length(self.step_no)
        print_memory_usage()

    def get_all_trajectories(self):
        """
        get trajectories of all atoms
        """
        self.atom_no = -1
        self._get_trajectories()
        return self.trajectory

    def get_single_trajectory(self, atom_no):
        """
        <atoms> can be either 
        The index of the first atom is 0.
        """
        self.atom_no = atom_no
        self._get_trajectories()
        self.traj['positions'] = self.traj['atoms'][0]['positions']
        return self.trajectory
    

class VasprunParser:
    """
    Parser for vasprun.xml files, making use of libxml for relatively fast parsing.
    """
    
    def __init__(self, filename = 'vasprun.xml', verbose = False):
        
        if not imported['lxml']:
            print "Error: The module 'lxml' is needed!"
            sys.exit(1)
        
        print_memory_usage()
        if verbose:
            print "Reading %s (%.2f MB)... " % (filename,os.path.getsize(filename)/1024.**2)

        self.filename = filename
        docstr = open(filename).read()

        # zap control characters that invalidates the xml
        docstr = re.sub('[\x00-\x09\x0B-\x1F]','',docstr)

        if verbose:
            print "Parsing... " 

        parser = etree.XMLParser() #recovers from bad characters.
        try:
            self.doc = etree.parse(StringIO(docstr), parser)
            #self.doc = etree.parse(self.filename, parser)
            for e in parser.error_log:
                print "Warning: "+e.message
        except etree.XMLSyntaxError:
            print "Failed to parse xml file: ",filename
            for e in parser.error_log:
                print "Warning: "+e.message
            sys.exit(2)
        print_memory_usage()
    
    def get_incar_property(self, propname):
        """ 
        Returns the value of a given INCAR property as a string,
        or throws a LookupError if the property was not found.
        Example: 
        >>> get_incar_property('ENCUT')
        """
        results = self.doc.xpath( "/modeling/incar/i[@name='"+propname+"']")
        if results:
            return results[0].text
        else:
            raise LookupError('Value not found')

    def get_num_kpoints(self):
        """Returns the number of k-points"""
        results = self.doc.xpath( "/modeling/kpoints/varray[@name='"+kpointlist+"']")
        if results:
            return results[0].text
        else:
            raise LookupError('Value not found')    
    
    def get_total_energy(self):
        """Returns the total energy in electronvolt"""
        results = self.doc.xpath( "/modeling/calculation/energy/i[@name='e_fr_energy']")
        if results:
            return float(results[0].text)
        else:
            raise LookupError('Value not found')
    

    def get_sc_steps(self):
        """
        Returns array of electronic self-consistent steps
        """
        results = self.doc.xpath( "/modeling/calculation/scstep")
        if results:
            return results
        else:
            raise LookupError('Value not found')

    def get_force_on_atom(self,atom_no):
        """
        Returns the force on atom <atom_no> as a numpy vector (x,y,z),
        where 1 is the first atom.
        """
        forces = self.doc.xpath( "/modeling/calculation/varray[@name='forces']/v")
        force = np.array([float(f) for f in forces[atom_no-1].text.split()])
        return force

    def get_initial_positions(self):
        """
        Returns the final position of all atoms as a (n,3) numpy array, where n is the number of atoms
        """
        all_pos = self.doc.xpath( "/modeling/structure[@name='initialpos']/varray[@name='positions']/v")
        num_atoms = len(all_pos)
        pos_array = np.zeros((num_atoms,3))
        for i in range(num_atoms):
            pos_array[i] = [float(f) for f in all_pos[i].text.split()]
        return pos_array

    def get_final_positions(self):
        """
        Returns the final position of all atoms as a (n,3) numpy array, where n is the number of atoms
        """
        all_pos = self.doc.xpath( "/modeling/structure[@name='finalpos']/varray[@name='positions']/v")
        num_atoms = len(all_pos)
        pos_array = np.zeros((num_atoms,3))
        for i in range(num_atoms):
            pos_array[i] = [float(f) for f in all_pos[i].text.split()]
        return pos_array
    
    def get_initial_velocities (self):
        """
        Returns the initial velocities of all atoms as a (n,3) numpy array, where n is the number of atoms
        """
        all_vel = self.doc.xpath( "/modeling/structure[@name='initialpos']/varray[@name='velocities']/v")
        if not all_vel:
            raise LookupError('Velocities not found. Is this file from a MD run?')

        num_atoms = len(all_vel)
        vel_array = np.zeros((num_atoms,3))
        for i in range(num_atoms):
            vel_array[i] = [float(f) for f in all_vel[i].text.split()]
        return vel_array

    def get_final_velocities(self):
        """
        Returns the final velocities of all atoms as a (n,3) numpy array, where n is the number of atoms
        """
        all_vel = self.doc.xpath( "/modeling/structure[@name='finalpos']/varray[@name='velocities']/v")
        if not all_vel:
            raise LookupError('Velocities not found. Is this file from a MD run?')

        num_atoms = len(all_vel)
        vel_array = np.zeros((num_atoms,3))
        for i in range(num_atoms):
            vel_array[i] = [float(f) for f in all_vel[i].text.split()]
        return vel_array

    def get_final_atom_position(self,atom_no):
        """
        Returns the final position of atom <atom_no> as a numpy vector (x,y,z)
        """
        pos = self.doc.xpath( "/modeling/structure[@name='finalpos']/varray[@name='positions']/v")
        atpos = np.array([float(f) for f in pos[atom_no-1].text.split()])
        return atpos
    
    def get_atom_trajectory(self,atom_no):
        """
        Returns a (n,3) numpy array with the position of atom <atom_no> for n timesteps.
        The index of the first atom is 0.
        """
        steps = self.doc.xpath( "/modeling/calculation" )
        num_steps = len(steps)
        traj = np.zeros((num_steps,3))
        i = 0
        for step in steps:
            pos = step.xpath( "structure/varray[@name='positions']/v[%d]" % (atom_no+1) )[0].text.split()
            traj[i] = [float(p) for p in pos]
            i += 1
        
        print "Found %d steps" % (i)

        return traj 

    def get_final_volume(self):
        """
        Returns the final volume in units Angstrom^3
        """
        results = self.doc.xpath( "/modeling/structure[@name='finalpos']/crystal/i[@name='volume']")
        if results:
            return float(results[0].text)
        else:
            raise LookupError('Value not found')

    def get_max_force(self):
        """
        Returns the max force acting on any atom
        """
        forces = self.doc.xpath( "/modeling/calculation/varray[@name='forces']/v")
        max_force = 0.
        for f in forces:
            force = np.array(f.text.split())
            force_norm = np.sqrt(np.dot(force,force))
            if force_norm > max_force:
                max_force = force_norm
        return max_force

    def get_cpu_time(self):
        """
        Returns the CPU time spent. The value returned corresponds to the the value found in
        OUTCAR files on a line like this: 
        
        >>> LOOP+:  cpu time  482.23: real time  482.58
        
        This will value is always somewhat lower than the value found in OUTCAR file on a 
        line like this:
        
        >>>         Total CPU time used (sec):      490.877
        
        but it appears like this value is not available from vasprun.xml files.
        """
        time = self.doc.xpath( "/modeling/calculation/time[@name='totalsc']")
        if time:
            time = time[0].text.split()
            return float(time[0])
        else:
            raise LookupError('Value not found in %s' % (self.filename))
        



class FileIterator:
    """
    Abstract iterator for reading files
    """

    def __init__(self, filename, cache = True):
        """
        If <cache> is set to True, the whole file is read in at once. 
        This is usually faster, but may not be possible for very large files.
        """
        self.filename = filename
        self.file = open(filename,'r')
        self.cached = cache
        # Caching is preferred unless the file is too big too keep in memory 
        if cache:
            self.contents = self.file.readlines()
            self.numlines = len(self.contents)
        self.reset()

    def __iter__(self):
        return self

    def next(self):
        self.lineno += 1
        if self.cached and self.lineno >= self.numlines:
            raise StopIteration
        elif not self.cached:
            try:
                return "1 "+self.file.readline()
            except GeneralError:
                raise StopIteration
        else:
            return "0 "+self.contents[self.lineno]

    def reset(self):
        self.lineno = -1
        if not self.cached:
            self.file.seek(0)


class OutcarParser:
    """
    Parser for OUTCAR files
    """

    def __init__(self, outcarname = 'OUTCAR', selective_dynamics = 0, verbose = False):
        
        if verbose:
            print "Parsing %s (%.1f MB)... " % (outcarname,os.path.getsize(outcarname)/1024.**2)

        self.filename = outcarname
        self.file = FileIterator(self.filename)
        self.selective_dynamics = selective_dynamics 
        
        # Read the first lines to find the following parameters:
        config = { 'IBRION': 0, 'NSW': 0, 'POTIM': 0., 'TEIN': 0., 'TEBEG': 0., 'TEEND': 0., 'SMASS': 0. }
        config_patterns = {}
        keys_found = {}
        for key in config.keys():
            keys_found[key] = False
            config_patterns[key] = re.compile(key+'[ \t]*=[ \t]*([0-9.\-]+)')

        for line in self.file:
            allkeys_found = True
            for key in config.keys():
                m = config_patterns[key].search(line)
                if m:
                    config[key] = float(m.group(1))
                    keys_found[key] = True
                if not keys_found[key]:
                    allkeys_found = False
            if allkeys_found:
                break
        if not allkeys_found:
            print "WARNING! Not all config keys were found! Perhaps the OUTCAR format has changed?"
            print "Keys not found:"
            for key in config.keys():
                if not keys_found[key]:
                    print key
        self.config = config 

        #print (self.file.lineno+1),"lines read"
        self.file.reset()

    def get_ionic_steps(self):
        """
        returns a dictionary with entries for time and energy.
        
        >>> d = get_ionic_steps()
        >>> plt.plot(d['time'],d['energy']['total'])
        
        This function could be optimized, since all the lines we are interested
        in for each step occurs after each other, we don't really have to search
        for each line
        """
        self.file.reset()
        numsteps = self.config['NSW']
        print " extracting ionic step data for %.0f steps..." % (numsteps)
        a = {
            'time': np.arange(1,numsteps+1),
            'energy': {
                'ion_electron': np.zeros((numsteps)),
                'ion_kinetic': np.zeros((numsteps)),
                'total': np.zeros((numsteps))
            },
            'forces': {
                'max_atom': np.zeros((numsteps)),
                'rms': np.zeros((numsteps))
            }
        }
        if self.config['IBRION'] == 0.:  # If MD
            a['time'] *= self.config['POTIM']

        for line in self.file:
            
            m = re.search('Iteration[ \t]*([0-9]+)', line)
            if m: 
                stepno = int(m.group(1))
                
            m = re.search('FORCES: max atom, RMS[ \t]*([0-9.\-]+)[ \t]*([0-9.\-]+)', line)
            if m: 
                a['forces']['max_atom'][stepno-1] = float(m.group(1))
                a['forces']['rms'][stepno-1] = float(m.group(2))

            m = re.search('% ion-electron   TOTEN[ \t]*=[ \t]*([0-9.\-]+)', line)
            if m: 
                a['energy']['ion_electron'][stepno-1] = float(m.group(1))

            m = re.search('kinetic Energy EKIN[ \t]*=[ \t]*([0-9.\-]+)', line)
            if m: 
                a['energy']['ion_kinetic'][stepno-1] = float(m.group(1))
            
            m = re.search('total energy   ETOTAL[ \t]*=[ \t]*([0-9.\-E]+)', line)
            if m: 
                a['energy']['total'][stepno-1] = float(m.group(1))

            #m = re.search('maximum distance moved by ions[ \t]*:[ \t]*([0-9.\-E]+)', line)
            #if m: 
            #    a['energy']['total'][stepno-1] = float(m.group(1))

        return a


    def readItAll(self):
        outfile = open(self.filename)
        while 1:
            line = outfile.readline()
            kmatch = re.search('(\d+) +irreducible', line)
            ematch = re.search('free  energy   TOTEN  = +(-*\d+.\d+)', line)
            cpumatch = re.search('Total CPU time used \(sec\): +(\d+.\d+)', line)
            distmatch = re.search('Following cartesian coordinates:', line)

            #k-points           NKPTS =      1   k-points in BZ     NKDIM =      1   number of bands    NBANDS=     96
            #number of dos      NEDOS =    301   number of ions     NIONS =      8
            #non local maximal  LDIM  =      4   non local SUM 2l+1 LMDIM =      8
            #total plane-waves  NPLWV =  32768

            planewavematch = re.search('NPLWV[ \t]*=[ \t]*([0-9])', line)
            nbandsmatch = re.search('NPLWV[ \t]*=[ \t]*([0-9])', line)
            if planewavematch:
                self.planewaves = int(planewavematch.group(1))
            if kmatch:
                self.kpoints = int(kmatch.group(1))
            elif ematch:
                self.toten= float(ematch.group(1))
            elif cpumatch:
                self.cpu = float(cpumatch.group(1))
            elif distmatch:
                if self.kpoints > 1:
                    tmpline = outfile.readline()
                    firstline = outfile.readline()
                    secondline = outfile.readline()
                    k1x,k1y,k1z,dummy = map(float, firstline.split())
                    k2x,k2y,k2z,dummy = map(float, secondline.split())
                    self.dist = math.sqrt((k2x-k1x)*(k2x-k1x)+(k2y-k1y)*(k2y-k1y)+(k2z-k1z)*(k2z-k1z))
                else:
                    self.dist = 0
            elif re.search(r'external pressure', line): 
                tmp,tmp,tmp,pressure,tmp,tmp,tmp,tmp,tmp,tmp = line.split()
                self.maxpressure = float(pressure)
            elif re.search(r'TOTAL\-FORCE', line):
                i=0
                line = outfile.readline()
                maxdrift = 0.0
                maxforce = 0.0
                while 1:
                    line = outfile.readline()
                    if re.search(r'----', line):
                        line = outfile.readline()
                        a,b,driftx,drifty,driftz = line.split()
                        if abs(float(driftx)) > maxdrift:
                            maxdrift = abs(float(driftx))
                        if abs(float(drifty)) > maxdrift:
                            maxdrift = abs(float(drifty))
                        if abs(float(driftz)) > maxdrift:
                            maxdrift = abs(float(driftz))
                        break
                    posx,posy,posz,forx,fory,forz = map(float, line.split())
                    if self.selective_dynamics:
                        if (abs(forx) > maxforce) and (x[i] == 'T' or x[i] == 't'):
                            maxforce = abs(forx)
                            maxi = i
                        if (abs(fory) > maxforce) and (y[i] == 'T' or y[i] == 't'):
                            maxforce = abs(fory)
                            maxi = i
                        if (abs(forz) > maxforce) and (z[i] == 'T' or z[i] == 't'):
                            maxforce = abs(forz)
                            maxi = i
                    else:
                        if abs(forx) > maxforce:
                            maxforce = abs(forx)
                            maxi = i
                        if abs(fory) > maxforce:
                            maxforce = abs(fory)
                            maxi = i
                        if abs(forz) > maxforce:
                            maxforce = abs(forz)
                            maxi = i
                        i = i+1
                self.maxforce = maxforce
                self.maxdrift = maxdrift
            if not line:
                break
        outfile.close()

    def get_max_drift(self):
        return self.maxdrift

    def get_max_pressure(self):
        return self.maxpressure

    def get_max_force(self):
        return self.maxforce

    def get_total_energy(self):
        return self.toten

    def get_cpu_time(self):
        return self.cpu

    def get_incar_property(self, propname):
        outfile = open(self.filename, 'r')
        lines = outfile.readlines()
        s = re.compile('[\t ]*'+propname+'[\t ]*=[\t ]*([0-9.]*)')
        for l in lines:
            res = s.match(l)
            if res:
                return res.group(1)

        print "Failed to lookup INCAR property "+propname+" in "+self.filename
        sys.exit(1)
    
    def get_num_kpoints(self):
        return self.kpoints

    #def read_stress(self):
    #    for line in open('OUTCAR'):
    #        if line.find(' in kB  ') != -1:
    #            stress = -np.array([float(a) for a in line.split()[2:]]) \
    #                     [[0, 1, 2, 4, 5, 3]] \
    #                     * 1e-1 * ase.units.GPa
    #    return stress
        

class PoscarParser:
    """
    Parser for POSCAR files
    """
    
    def __init__(self, poscarname='POSCAR'):
        self.selective_dynamics = False
        self.filename = poscarname
        self._parse()

    def _parse(self):
        poscarfile = open( self.filename, 'r')  # r for reading
        commentline = poscarfile.readline()
        self.scale_factor = float(poscarfile.readline()) # lattice constant
        vec1line = poscarfile.readline()
        vec2line = poscarfile.readline()
        vec3line = poscarfile.readline()
        self.basis = np.zeros((3,3))
        self.basis[0] = map(float,vec1line.split())
        self.basis[1] = map(float,vec2line.split())
        self.basis[2] = map(float,vec3line.split())

        sixthline = poscarfile.readline()  # Test for vasp5 syntax
        try:
            dummy = int(sixthline.split()[0])
            atomnumberline = sixthline
        except:
            atomnumberline = poscarfile.readline()
        self.atomnumbers = map(int,atomnumberline.split())
        self.natoms = sum(self.atomnumbers)
        seventhline = poscarfile.readline()

        if seventhline[0] == 'S' or seventhline[0] == 's':
            self.selective_dynamics = True
            seventhline = poscarfile.readline()
        else:
            self.selective_dynamics = False

        self.coords = np.zeros((self.natoms,3))
        for j in range(self.natoms):
            line = poscarfile.readline()  # read a line
            self.coords[j] = [float(x) for x in (line.split()[0:3])]
        
        poscarfile.close()
    
    def get_coords(self):
        return self.coords

    def get_basis(self):
        return self.basis

    def get_scale_factor(self):
        """ lattice constant"""
        return self.scale_factor

