from distutils.core import setup
setup(name='pyang',
      version='0.9',
      author='Martin Bjorklund',
      description="A YANG validator and converter",
      scripts=['bin/pyang'],
      packages=['pyang', 'pyang.plugins'],
      )